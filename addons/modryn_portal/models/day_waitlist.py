import logging
import secrets
from datetime import datetime, timedelta

import pytz
from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models

from .sms import normalize_il_phone

_logger = logging.getLogger(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# She gets first refusal for two hours. Long enough to be at work and still
# answer; short enough that a freed Saturday slot is not dead all afternoon.
CLAIM_WINDOW_HOURS = 2

# How far down the queue one modryn_offer_next() call will walk past candidates
# it cannot text. A bound, not a policy: every skipped candidate is written
# 'expired' first so the walk always makes progress, but a runaway loop inside a
# cancellation request is not a failure mode worth leaving open.
MAX_OFFER_CANDIDATES = 5

# Postgres reports these in diag.constraint_name. Compared by name, never by
# catching the class: this table also carries a pkey and two fkeys, and a
# violation from one of those is a different bug that must not be swallowed.
PHONE_DAY_INDEX = 'modryn_day_waitlist_phone_day_uniq'
ONE_OFFER_PER_DAY_INDEX = 'modryn_day_waitlist_modryn_one_offer_per_day'


class ModrynDayWaitlist(models.Model):
    """Someone who wants a day the calendar cannot currently give her.

    Deliberately a DAY, not a slot: most cancellations will not match anyone's
    exact hour, so a per-slot list would almost never fire. She said "Tuesday
    works" — the boutique honours that.
    """

    _name = 'modryn.day.waitlist'
    _description = 'Waiting for a day to open up'
    _order = 'create_date asc, id asc'

    name = fields.Char(required=True)
    phone = fields.Char(required=True, index=True)
    day = fields.Date(required=True, index=True)
    state = fields.Selection(
        selection=[
            ('waiting', 'Waiting'),
            ('offered', 'Offered'),
            ('claimed', 'Claimed'),
            ('expired', 'Expired'),
        ],
        default='waiting',
        required=True,
        index=True,
    )
    offer_token = fields.Char(copy=False, index=True)
    offer_expires_at = fields.Datetime()
    # The offer SMS is composed by a CRON, whose language is the server's, not
    # hers. Without capturing the language she was reading when she joined, a
    # Hebrew customer gets an English text.
    lang = fields.Char(default='he_IL')

    _phone_day_uniq = models.Constraint(
        'unique(phone, day)', "You are already on the waitlist for that day.")

    # One standing offer per day, decided by the database. modryn_offer_next's
    # search_count is a read-then-write with no lock — structurally the same bug
    # the calendar_event slot index exists to fix. Two workers both see zero
    # offered, both pick the front of the queue, both write: the second
    # offer_token overwrites the first, she gets two texts, and the first link
    # she taps renders claim_expired. It is reachable today because an HTTP
    # cancel (modryn_cancel) and two crons (offer expiry, and the outbox drain
    # via _modryn_offer_undeliverable) all call modryn_offer_next.
    #
    # Partial on state: 'waiting', 'expired' and 'claimed' rows must be free to
    # pile up on one day — a queue of ten for Tuesday is the entire point.
    _modryn_one_offer_per_day = models.UniqueIndex(
        "(day) WHERE state = 'offered'",
        "Someone else already holds the offer for that day.",
    )

    # ------------------------------------------------------------------- join
    @api.model
    def modryn_join(self, name, phone, day, lang=None):
        """Add her, or hand back the place she already holds."""
        normalized = normalize_il_phone(phone)
        if not normalized:
            return False, 'invalid_number', None
        # EVERY state, not just the live ones. _phone_day_uniq is on (phone, day)
        # alone, so filtering to waiting/offered sent a customer whose offer had
        # lapsed straight into create() and a 500 on a public POST — and nothing
        # ever un-expires a row, so that 500 was permanent for her on that day.
        existing = self.sudo().search([
            ('phone', '=', normalized), ('day', '=', day)], limit=1)
        if existing:
            # 'expired' ONLY, never 'claimed'. A claimed row is the record that
            # this offer turned into a booking; reviving it would destroy that
            # history and re-queue a customer who already holds an hour that day,
            # so the next cancellation would text her a second claim link for a
            # day she is already booked on.
            if existing.state == 'expired':
                # Her turn came and went (or her number was unreachable then).
                # Asking again puts her back in the queue; she keeps her original
                # create_date, so she returns to the place she had rather than
                # jumping anyone who joined while she was expired.
                existing.write({
                    'state': 'waiting', 'offer_token': False, 'offer_expires_at': False})
            return True, 'already_waiting', existing
        vals = {
            'name': (name or '').strip() or normalized,
            'phone': normalized,
            'day': day,
            'lang': lang or self.env.lang or 'he_IL',
        }
        try:
            # Two joins for the same number and day landed together; the search
            # above cannot see the other one until it commits. Savepoint, not a
            # bare try: a rejected INSERT aborts the whole transaction, so the
            # re-search below would fail too.
            with self.env.cr.savepoint():
                entry = self.sudo().create(vals)
        except UniqueViolation as exc:
            if exc.diag.constraint_name != PHONE_DAY_INDEX:
                raise
            entry = self.sudo().search([
                ('phone', '=', normalized), ('day', '=', day)], limit=1)
            return True, 'already_waiting', entry
        return True, None, entry

    # ------------------------------------------------------------------ offer
    def _base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

    def _make_offer(self):
        """Text her the claim link and start her exclusive window."""
        self.ensure_one()
        # Compose in the language she joined in, not the cron's.
        self = self.with_context(lang=self.lang or 'he_IL')
        self.write({
            'state': 'offered',
            'offer_token': secrets.token_urlsafe(16),
            'offer_expires_at': datetime.utcnow() + timedelta(hours=CLAIM_WINDOW_HOURS),
        })
        # Force the UPDATE now, before an SMS is queued. Odoo defers write() to
        # the next flush, which would otherwise land AFTER send_async: a racer
        # losing _modryn_one_offer_per_day would be rolled back having already
        # enqueued a claim link for an offer that no longer exists — an SMS
        # cannot be un-sent. modryn_offer_next catches the violation.
        self.flush_recordset()
        body = _("A place has opened up on %(date)s. It's yours for the next "
                 "%(hours)s hours: %(link)s") % {
            'date': self.day.strftime('%d.%m.%Y'),
            'hours': CLAIM_WINDOW_HOURS,
            'link': '%s/claim/%s' % (self._base_url(), self.offer_token),
        }
        # One shared Twilio number across every boutique now, so a claim link
        # from an unknown number is unattributable — and this one asks her to act
        # within two hours. Prefixed rather than woven into the sentence for the
        # reason queue_entry._send spells out. Read here, under the with_context
        # above, and not before it: res.company.name is related to
        # res.partner.name, which is translate=True, so composing it in the
        # cron's language would put an English boutique name on a Hebrew text.
        body = '%s: %s' % (self.env.company.name, body)
        # Queued: _make_offer sits on the customer-facing cancel path
        # (modryn_cancel -> modryn_offer_next), so the bride cancelling would
        # otherwise pay Twilio's latency to text a stranger.
        #
        # A number Twilio accepts and then rejects (landline, unsubscribed, bad
        # To) answers True here and fails later, inside the outbox. That is why
        # the row carries waitlist_id: on a PERMANENT final rejection the drain
        # calls _modryn_offer_undeliverable() below and the day is handed back,
        # instead of one dead number blocking the whole line for two hours. On a
        # transient one — our credentials, Twilio's 5xx, a rate limit — it does
        # not, because that failure is not hers to pay for.
        ok, detail = self.env['modryn.sms'].send_async(
            self.phone, body, waitlist_id=self.id)
        if not ok:
            # Legacy-data guard, and only that. send_async refuses only what
            # normalize_il_phone refuses, and `phone` is already its output —
            # which, since the bare-'972' branch got its missing length check,
            # re-parses to itself. So this can now fire only for a row written
            # before that fix (both tenants: zero such rows). Kept because the
            # cost is three lines and the alternative is a 'waiting' entry the
            # queue retries forever. Returning False no longer strands the day:
            # modryn_offer_next walks on to the next candidate.
            _logger.warning('[modryn.waitlist] offer sms refused for %s (%s): %s',
                            self.id, self.phone, detail)
            self.write({'state': 'expired', 'offer_token': False, 'offer_expires_at': False})
            return False
        return True

    def _modryn_offer_undeliverable(self):
        """The offer text will never arrive — hand the day back to the queue now.

        Called by modryn.sms.outbox when a queued offer exhausts its attempts.
        Without it a permanently undeliverable number holds the ENTIRE day: only
        one offer stands per day at a time, so nobody else is texted until the
        2h window lapses on a message that was never going to be read.
        """
        self.ensure_one()
        # She may have claimed through an earlier offer, or the expiry cron may
        # have got here first. Only an offer still standing is ours to withdraw.
        if self.state != 'offered':
            return
        _logger.warning('[modryn.waitlist] offer %s undeliverable, re-offering %s',
                        self.id, self.day)
        self.write({'state': 'expired', 'offer_token': False})
        self.modryn_offer_next(self.day)

    @api.model
    def modryn_offer_next(self, day):
        """A slot freed on `day` — give the front of the line first refusal.

        One offer at a time per day: two people holding claim links for one
        slot is a race the boutique loses in public.
        """
        if self.sudo().search_count([('day', '=', day), ('state', '=', 'offered')]):
            return None
        # Plural. `limit=1` plus `if candidate and candidate._make_offer()` meant
        # ONE untextable entry at the front of the queue silently ended the day:
        # she was expired, None was returned, and the next person was never
        # tried by anyone — _modryn_expire_offers only revisits state='offered'.
        candidates = self.sudo().search(
            [('day', '=', day), ('state', '=', 'waiting')],
            order='create_date asc', limit=MAX_OFFER_CANDIDATES)
        for candidate in candidates:
            try:
                # Savepoint so a lost race does not abort the caller's whole
                # transaction — modryn_offer_next runs inside modryn_cancel(),
                # and her cancellation must succeed either way.
                with self.env.cr.savepoint():
                    if candidate._make_offer():
                        return candidate
            except UniqueViolation as exc:
                if exc.diag.constraint_name != ONE_OFFER_PER_DAY_INDEX:
                    raise
                # Another worker made this day's offer between our search_count
                # and our write. The invariant held and someone was texted, so
                # this is the outcome we wanted, not an error. Stop: a second
                # offer is exactly what the index exists to prevent.
                _logger.info('[modryn.waitlist] %s already has a standing offer; '
                             'another worker got there first', day)
                return None
            _logger.warning('[modryn.waitlist] cannot text waitlist %s for %s, '
                            'trying the next in line', candidate.id, day)
        return None

    @api.model
    def _modryn_expire_offers(self):
        """Unclaimed offers fall through to the next person in line."""
        stale = self.sudo().search([
            ('state', '=', 'offered'),
            ('offer_expires_at', '<', datetime.utcnow()),
        ])
        days = set(stale.mapped('day'))
        stale.write({'state': 'expired', 'offer_token': False})
        for day in days:
            self.modryn_offer_next(day)
