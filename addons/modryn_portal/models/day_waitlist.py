import logging
import secrets
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models

from .sms import normalize_il_phone

_logger = logging.getLogger(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# She gets first refusal for two hours. Long enough to be at work and still
# answer; short enough that a freed Saturday slot is not dead all afternoon.
CLAIM_WINDOW_HOURS = 2


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

    # ------------------------------------------------------------------- join
    @api.model
    def modryn_join(self, name, phone, day, lang=None):
        """Add her, or hand back the place she already holds."""
        normalized = normalize_il_phone(phone)
        if not normalized:
            return False, 'invalid_number', None
        existing = self.sudo().search([
            ('phone', '=', normalized), ('day', '=', day),
            ('state', 'in', ('waiting', 'offered')),
        ], limit=1)
        if existing:
            return True, 'already_waiting', existing
        entry = self.sudo().create({
            'name': (name or '').strip() or normalized,
            'phone': normalized,
            'day': day,
            'lang': lang or self.env.lang or 'he_IL',
        })
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
        body = _("A place has opened up on %(date)s. It's yours for the next "
                 "%(hours)s hours: %(link)s") % {
            'date': self.day.strftime('%d.%m.%Y'),
            'hours': CLAIM_WINDOW_HOURS,
            'link': '%s/claim/%s' % (self._base_url(), self.offer_token),
        }
        ok, detail = self.env['modryn.sms'].send(self.phone, body)
        if not ok:
            # An offer she never received would hold the slot hostage for two
            # hours, so hand it straight back to the queue.
            _logger.warning('[modryn.waitlist] offer sms failed for %s: %s', self.id, detail)
            self.write({'state': 'expired'})
            return False
        return True

    @api.model
    def modryn_offer_next(self, day):
        """A slot freed on `day` — give the front of the line first refusal.

        One offer at a time per day: two people holding claim links for one
        slot is a race the boutique loses in public.
        """
        if self.sudo().search_count([('day', '=', day), ('state', '=', 'offered')]):
            return None
        candidate = self.sudo().search(
            [('day', '=', day), ('state', '=', 'waiting')],
            order='create_date asc', limit=1)
        if candidate and candidate._make_offer():
            return candidate
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
