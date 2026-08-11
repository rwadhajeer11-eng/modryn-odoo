import logging
from datetime import timedelta

from odoo import api, fields, models

from .sms import is_permanent_rejection

_logger = logging.getLogger(__name__)

# Bounded on purpose. An unbounded drain would hold one transaction open for
# batch x SEND_TIMEOUT in the worst case; when the batch comes back full we
# re-trigger immediately, so a backlog still clears fast without one giant run.
DRAIN_BATCH = 50

# Three shots, then stop. Twilio failing three times over several minutes is not
# a blip, and a row that retries forever is a pager at 3am, not a delivery.
MAX_ATTEMPTS = 3
# Minutes to wait before attempt N+1; one entry per retry, so MAX_ATTEMPTS - 1.
RETRY_BACKOFF_MINUTES = (1, 5)
# The drain indexes this by attempt number. Tuning one constant without the other
# is an IndexError at 3am inside a cron nobody is watching, so fail at import.
assert len(RETRY_BACKOFF_MINUTES) == MAX_ATTEMPTS - 1

DRAIN_CRON = 'modryn_portal.ir_cron_sms_outbox_drain'

# A finished row's body carries her name and a live booking link, so this is a
# privacy retention limit first and a disk one second. A week is long enough to
# answer "did she get the text?" on Monday about Saturday, and short enough that
# the boutique is not sitting on a year of customer messages it never reads.
RETENTION_DAYS = 7


class ModrynSmsOutbox(models.Model):
    """One row per queued text.

    Deliberately an outbox and not a job queue: it stores a number and a body,
    never anything callable. That is exactly what makes it safe to drain from a
    cron that may be running different code than the request which enqueued the
    row — and what stops this from growing into a job framework nobody asked for.
    """

    _name = 'modryn.sms.outbox'
    _description = 'Queued outbound SMS'
    # Fair order: first queued is first sent. When one cancellation frees a day,
    # the offer texted to the front of the waitlist goes out before the booking
    # confirmation of whoever books a minute later — the person who has been
    # waiting longest does not lose her head start to the queue.
    _order = 'id asc'

    phone = fields.Char(required=True, index=True)
    body = fields.Text(required=True)
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('sent', 'Sent'),
            ('failed', 'Failed'),
        ],
        default='pending',
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0, readonly=True)
    last_error = fields.Char(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    # Backoff needs somewhere to live. Without it the very next enqueue's
    # _trigger() would re-drain a row that just failed, and all three attempts
    # would burn inside one second — a retry policy in name only.
    retry_after = fields.Datetime(readonly=True)
    # Who is waiting on this message, when someone's state depends on it landing.
    # Only the waitlist offer sets it: that entry holds its whole day hostage
    # until it knows, so final failure has to travel back. Deliberately an id and
    # not a stored callable — see the class docstring.
    waitlist_id = fields.Many2one(
        'modryn.day.waitlist', ondelete='set null', index=True, readonly=True)

    # ------------------------------------------------------------------- cron
    @api.model
    def _drain_cron(self):
        return self.env.ref(DRAIN_CRON, raise_if_not_found=False)

    @api.model
    def _wake(self, at=None):
        cron = self._drain_cron()
        if not cron:
            # Never raise on the caller's behalf: a missing cron record must cost
            # a late text, not the booking she just made.
            _logger.warning('[modryn.sms] drain cron %s missing; queued rows wait', DRAIN_CRON)
            return
        # _trigger() queues a pg_notify('cron_trigger') post-commit, so the drain
        # wakes about a second after the request returns. The cron's own interval
        # is only the fallback for a notify nobody was listening for. at=None is
        # the API's own "as soon as possible".
        cron.sudo()._trigger(at=at)

    # ---------------------------------------------------------------- enqueue
    @api.model
    def _enqueue(self, phone, body, waitlist_id=None):
        """Store the message and wake the drain. `phone` must already be E.164."""
        row = self.sudo().create({
            'phone': phone, 'body': body, 'waitlist_id': waitlist_id})
        self._wake()
        return row

    # --------------------------------------------------------------- reap/hook
    @api.model
    def _gc(self):
        """Drop finished rows past retention."""
        cutoff = fields.Datetime.now() - timedelta(days=RETENTION_DAYS)
        old = self.sudo().search([
            ('state', 'in', ('sent', 'failed')),
            ('create_date', '<', cutoff),
        ])
        if old:
            _logger.info('[modryn.sms] reaping %s outbox rows older than %sd',
                         len(old), RETENTION_DAYS)
            old.unlink()

    def _release_waitlist(self):
        """Hand her day back ONLY if her number is the thing that failed.

        Reclaiming on any final failure turned a Twilio outage into a scythe: a
        wrong API key answers 401 for everybody, so A was offered, failed three
        times in ~6 minutes, was expired, B was offered, and a ten-deep list for
        one day emptied itself within the hour with nobody notified. Our own
        fault must not cost her her place — on a transient failure the offer is
        left standing and the 2h expiry cron moves the queue on as it always did.
        """
        self.ensure_one()
        if not self.waitlist_id:
            return
        if not is_permanent_rejection(self.last_error):
            _logger.warning(
                '[modryn.sms] outbox %s failed transiently (%s); leaving waitlist '
                '%s offered for the expiry cron', self.id, self.last_error,
                self.waitlist_id.id)
            return
        try:
            # Savepoint, not a bare except: in postgres a failed statement aborts
            # the whole transaction, so catching without one would leave this
            # row's 'failed' write unwritable and hand the drain back the very
            # poison row it just retired.
            with self.env.cr.savepoint():
                self.waitlist_id._modryn_offer_undeliverable()
        except Exception:
            # The row is already 'failed'; a broken reclaim must not take the
            # queue with it. The 2h expiry cron is still the backstop.
            _logger.exception('[modryn.sms] waitlist reclaim failed for outbox %s', self.id)

    # ------------------------------------------------------------------ drain
    @api.model
    def _drain(self):
        """Send what is waiting, oldest first, and schedule the next wake-up."""
        self._gc()
        now = fields.Datetime.now()
        pending = self.sudo().search([
            ('state', '=', 'pending'),
            '|', ('retry_after', '=', False), ('retry_after', '<=', now),
        ], limit=DRAIN_BATCH)
        if not pending:
            return

        sender = self.env['modryn.sms']
        wakeups = []
        for row in pending:
            try:
                # _send_now promises never to raise. This guard is what makes
                # that a contract rather than a hope: an escaping row is never
                # marked, so _order='id asc' re-picks it first on every run and
                # wedges every message behind it — and five consecutive cron
                # failures a week apart deactivate the drain outright
                # (ir_cron.MIN_FAILURE_COUNT_BEFORE_DEACTIVATION), silently
                # ending all SMS. Burning an attempt ages a poison row out to
                # 'failed' instead.
                with self.env.cr.savepoint():
                    ok, detail = sender._send_now(row.phone, row.body)
            except Exception:
                _logger.exception('[modryn.sms] outbox %s raised; counting as a failure', row.id)
                ok, detail = False, 'raised'
            attempts = row.attempts + 1
            if ok:
                row.write({
                    'state': 'sent',
                    'sent_at': fields.Datetime.now(),
                    'attempts': attempts,
                    'last_error': False,
                    'retry_after': False,
                })
            elif attempts >= MAX_ATTEMPTS:
                _logger.warning('[modryn.sms] giving up on outbox %s after %s attempts: %s',
                                row.id, attempts, detail)
                row.write({'state': 'failed', 'attempts': attempts, 'last_error': detail})
                # Somebody's day may be blocked on this text. Say so now.
                row._release_waitlist()
            else:
                delay = RETRY_BACKOFF_MINUTES[attempts - 1]
                # From the clock NOW, not the `now` captured before the loop.
                # Each row can burn up to SEND_TIMEOUT, so under the exact
                # failure this backoff exists for — a timing-out Twilio — a full
                # batch takes minutes and every retry from roughly the sixth row
                # on was stamped in the past. _wake() then fired immediately and
                # attempts 2 and 3 burned back-to-back against a still-degraded
                # Twilio: a backoff of zero, precisely when it was needed.
                retry_at = fields.Datetime.now() + timedelta(minutes=delay)
                _logger.warning('[modryn.sms] outbox %s attempt %s failed (%s), retry in %smin',
                                row.id, attempts, detail, delay)
                row.write({'attempts': attempts, 'last_error': detail, 'retry_after': retry_at})
                wakeups.append(retry_at)
            # Commit per row. A worker recycled mid-batch must not re-send the
            # texts it has already delivered; Odoo's own mail queue commits for
            # exactly this reason.
            self.env.cr.commit()

        if len(pending) == DRAIN_BATCH:
            # The batch came back full, so there is more behind it.
            wakeups.append(now)
        if wakeups:
            self._wake(at=wakeups)
