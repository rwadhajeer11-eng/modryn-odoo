import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# The reminder fires a day ahead. The cron runs every 15 minutes and claims a
# 30-minute window, so a restart or a slow run cannot skip an appointment — the
# reminded-at stamp is what stops the overlap from texting twice.
REMINDER_LEAD_HOURS = 24
REMINDER_WINDOW_MINUTES = 30


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    modryn_reminder_sent_at = fields.Datetime(readonly=True)
    modryn_confirmed_at = fields.Datetime(readonly=True)
    # The language she booked in. Without it every later message would be
    # Hebrew, including to the customer who booked the whole thing in Arabic.
    modryn_lang = fields.Char(default='he_IL')

    # ------------------------------------------------------------------ token
    def _modryn_token(self):
        """An unguessable, stable handle for this booking.

        A signed token rather than a stored one: nothing to expire, nothing to
        clean up, and it cannot be enumerated by walking ids the way /b/17 could.
        """
        self.ensure_one()
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or ''
        digest = hmac.new(secret.encode(), ('booking:%s' % self.id).encode(),
                          hashlib.sha256).hexdigest()[:24]
        return '%s-%s' % (self.id, digest)

    @api.model
    def _modryn_from_token(self, token):
        try:
            event_id = int((token or '').split('-')[0])
        except (ValueError, IndexError):
            return self.browse()
        event = self.sudo().browse(event_id).exists()
        # Compare digests, never trust the id half on its own.
        if not event or not event.modryn_is_booking:
            return self.browse()
        return event if hmac.compare_digest(event._modryn_token(), token or '') else self.browse()

    # ------------------------------------------------------------------- copy
    def _modryn_local(self):
        self.ensure_one()
        return pytz.utc.localize(self.start).astimezone(TZ)

    def _modryn_sms_env(self):
        """Render messages in the language she actually booked in."""
        self.ensure_one()
        return self.with_context(lang=self.modryn_lang or 'he_IL')

    def _modryn_base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

    # ------------------------------------------------------------- send hooks
    def modryn_send_confirmation(self):
        """Text her the booking. A failed SMS must never lose a real booking."""
        for event in self:
            if not event.modryn_customer_phone:
                continue
            body = event._modryn_sms_env()._modryn_body('confirmation')
            # Queued, not sent inline: this runs on POST /book/submit, and a
            # degraded Twilio used to hold that worker for SEND_TIMEOUT per
            # booking. Nothing here reads the result beyond logging it, so there
            # is nothing to preserve by waiting.
            ok, detail = event.env['modryn.sms'].send_async(event.modryn_customer_phone, body)
            if not ok:
                _logger.warning('[modryn.comms] confirmation not sent for %s: %s',
                                event.id, detail)

    def _modryn_body(self, kind):
        self.ensure_one()
        local = self._modryn_local()
        values = {
            'boutique': self.env.company.name,
            'date': local.strftime('%d.%m.%Y'),
            'time': local.strftime('%H:%M'),
            'link': '%s/b/%s' % (self._modryn_base_url(), self._modryn_token()),
        }
        if kind == 'confirmation':
            return _("Your fitting at %(boutique)s is booked for %(date)s at %(time)s. "
                     "Manage your appointment: %(link)s") % values
        return _("Reminder: your fitting at %(boutique)s is tomorrow, %(date)s at "
                 "%(time)s. Confirm or cancel: %(link)s") % values

    # ------------------------------------------------------------------- cron
    @api.model
    def _modryn_send_reminders(self):
        """Text everyone whose fitting is ~24h away, exactly once."""
        now = datetime.utcnow()
        window_start = now + timedelta(hours=REMINDER_LEAD_HOURS)
        window_end = window_start + timedelta(minutes=REMINDER_WINDOW_MINUTES)

        domain = [
            ('modryn_is_booking', '=', True),
            ('modryn_reminder_sent_at', '=', False),
            ('start', '>=', window_start),
            ('start', '<', window_end),
        ]
        # A cancelled fitting must never be reminded about.
        if 'modryn_cancelled_at' in self._fields:
            domain.append(('modryn_cancelled_at', '=', False))

        for event in self.sudo().search(domain):
            if not event.modryn_customer_phone:
                continue
            body = event._modryn_sms_env()._modryn_body('reminder')
            # Stays SYNCHRONOUS on purpose, alone among the non-interactive
            # senders. This is a cron: there is no HTTP worker here to pin, so
            # queueing buys nothing — and it would cost the one thing that makes
            # the reminder reliable. The stamp below is the retry ledger; if we
            # stamped on "enqueued" instead, a row that later exhausts its
            # attempts leaves an event marked reminded that nobody was reminded
            # about, lost forever. Un-stamping it would need the outbox to call
            # back into the event, which is the job framework this is not.
            ok, detail = self.env['modryn.sms'].send(event.modryn_customer_phone, body)
            # Stamp on success only, so a transport blip retries next quarter hour
            # rather than silently dropping the reminder for good.
            if ok:
                event.modryn_reminder_sent_at = fields.Datetime.now()
            else:
                _logger.warning('[modryn.comms] reminder not sent for %s: %s',
                                event.id, detail)
