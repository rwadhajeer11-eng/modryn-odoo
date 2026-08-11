import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import pytz

# Guarded exactly the way core guards it (addons/calendar/models/calendar_event.py)
# so this module still imports on a server without it. Core already logs the
# warning; a second one would only be noise.
try:
    import vobject
except ImportError:
    vobject = None

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

        The database name is IN the signed message, not just implied by the key.
        Under DB-per-boutique the key alone looked like enough — until it wasn't:
        new_boutique.sh clones with `createdb -T`, which copies database.secret,
        and nothing rotated it. Every tenant shared one key, ids restart at 1 in
        each database, so bella's token for booking 7 was byte-identical to
        noga's. One boutique's reminder link opened, confirmed and cancelled
        another boutique's appointment. new_boutique.sh now rotates the secret;
        this line means a clone path that forgets to, some day, still cannot
        collide.
        """
        self.ensure_one()
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or ''
        msg = ('booking:%s:%s' % (self.env.cr.dbname, self.id)).encode()
        digest = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:24]
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

    # --------------------------------------------------------------- calendar
    def _modryn_ics(self):
        """This booking as a .ics, or b'' when the server has no vobject.

        Stock _get_ics_file() does all the VCALENDAR work. Three things it
        leaves behind are wrong for a file a customer keeps:

          * vobject invents the UID at serialize time out of the clock, the pid
            and the server's HOSTNAME — verified, two runs a second apart gave
            two different UIDs. Every download would be a NEW event: tap the
            link on the confirmation page and again on the reminder page and she
            has two fittings, and our hostname rides along in a file that lives
            in her calendar. A UID derived from the booking id fixes both, and
            makes a re-download UPDATE the entry she already has.
          * every MODRYN partner is phone-only, so the attendee loop emits a
            bare `ATTENDEE:MAILTO:` — not a CAL-ADDRESS, and enough to make
            Outlook offer accept/decline on what is a personal appointment.
            There is no ORGANIZER to pair it with either (the organizer partner
            has no email), so the line carries nothing at all.
          * the stock DESCRIPTION is Odoo's contact block: "Organized by /
            Public user" — stale, the booking is reassigned away from the public
            user immediately after creation — then the customer's OWN name and
            phone, with the tel: anchor mangled by html2plaintext into
            "0521234567 [1] ... [1] tel:0521234567". She needs none of that. The
            way back to confirm or cancel is the one thing worth carrying.

        Reparsing what stock just serialized, rather than overriding
        _get_ics_file: four lines instead of re-implementing sixty, and the day
        core changes its VCALENDAR we inherit the change. Not via
        _get_customer_description either — google_calendar and
        microsoft_calendar both call that, and this token belongs in neither.
        """
        self.ensure_one()
        content = self._get_ics_file().get(self.id)
        if not content:
            return b''
        cal = vobject.readOne(content.decode())
        # pop-then-add rather than assigning to .value: a VEVENT takes at most
        # one of each, and pop cannot AttributeError on a line vobject omitted.
        cal.vevent.contents.pop('attendee', None)
        cal.vevent.contents.pop('uid', None)
        cal.vevent.add('uid').value = 'modryn-booking-%s@%s' % (
            self.id, urlsplit(self._modryn_base_url()).hostname or 'modryn')
        cal.vevent.contents.pop('description', None)
        cal.vevent.add('description').value = '%s/b/%s' % (
            self._modryn_base_url(), self._modryn_token())
        return cal.serialize().encode()

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
