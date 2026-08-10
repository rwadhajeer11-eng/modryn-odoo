import hashlib
import hmac
import logging
import secrets
from datetime import datetime

from odoo import _, api, fields, models

from odoo.addons.modryn_portal.models.sms import normalize_il_phone

_logger = logging.getLogger(__name__)

# One channel per database is enough: a tenant IS a database here, so there is
# no cross-boutique leak to design around — the websocket is already scoped.
QUEUE_CHANNEL = 'modryn_queue'

# A ticket is live from the moment she scans until she is served or sent home.
OPEN_STATES = ('pending', 'waiting', 'called')


class ModrynQueueEntry(models.Model):
    _name = 'modryn.queue.entry'
    _description = 'Walk-in queue entry'
    # Fair order is the whole point of a queue: first to submit is first served.
    _order = 'create_date asc, id asc'

    name = fields.Char(required=True)
    phone = fields.Char()
    client_type = fields.Selection(
        selection=[('bride', 'Bride'), ('evening', 'Evening')],
        default='bride',
        required=True,
    )
    # pending is the new front door: a scan puts her here, and a staff member
    # accepts her into the line. She never sees this gate — from her side the
    # boutique simply says "we'll be with you soon" either way.
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('waiting', 'Waiting'),
            ('called', 'Called'),
            ('done', 'Finished'),
            ('redirected', 'Invited to book'),
            ('expired', 'Expired'),
        ],
        default='pending',
        required=True,
        index=True,
    )

    # Her private page. Random, not derived from the id: two customers must not
    # be able to guess each other's ticket by counting.
    access_token = fields.Char(index=True, copy=False)
    next_notified_at = fields.Datetime(readonly=True)
    turn_notified_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('access_token', secrets.token_urlsafe(16))
        entries = super().create(vals_list)
        entries._notify_board()
        return entries

    def _payload(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or '',
            'client_type': self.client_type,
            'state': self.state,
        }

    def _notify_board(self):
        """Push to every open board. Called after the row is committed-visible."""
        for entry in self:
            self.env['bus.bus']._sendone(QUEUE_CHANNEL, 'modryn_queue/update', entry._payload())

    def write(self, vals):
        res = super().write(vals)
        # Only state changes matter to the board; anything else is noise.
        if 'state' in vals:
            self._notify_board()
        return res

    # --------------------------------------------------------------- lifecycle
    @api.model
    def modryn_check_in(self, name, phone, client_type='bride'):
        """Take a walk-in, or hand back the ticket she already has.

        Re-scanning the sign, or a second person typing the same number, must
        never create a rival ticket — she would appear twice in the line and
        lose her real place.
        """
        normalized = normalize_il_phone(phone) if phone else None
        if normalized:
            existing = self.sudo().search([
                ('phone', '=', normalized), ('state', 'in', OPEN_STATES),
            ], limit=1)
            if existing:
                return existing
        return self.sudo().create({
            'name': name,
            'phone': normalized or (phone or '').strip(),
            'client_type': client_type,
        })

    def modryn_accept(self):
        """Staff let her into the line. Idempotent: two managers, one transition."""
        for entry in self.filtered(lambda e: e.state == 'pending'):
            entry.state = 'waiting'
        self._notify_next_in_line()
        return self

    def modryn_redirect(self, notify=True):
        """Too busy today — invite her to book instead, warmly."""
        for entry in self.filtered(lambda e: e.state in ('pending', 'waiting')):
            entry.state = 'redirected'
            if notify and entry.phone:
                entry._send(_(
                    "We're fully booked today. Book a fitting with us here: %(link)s"
                ) % {'link': '%s/book' % entry._base_url()})
        return self

    # ------------------------------------------------------------------- comms
    def _base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

    def _ticket_url(self):
        self.ensure_one()
        return '%s/q/%s' % (self._base_url(), self.access_token)

    def _send(self, body):
        self.ensure_one()
        if not self.phone:
            return
        ok, detail = self.env['modryn.sms'].send(self.phone, body)
        if not ok:
            _logger.warning('[modryn.queue] sms not sent for %s: %s', self.id, detail)

    @api.model
    def _notify_next_in_line(self):
        """Text whoever is now at the front — once, ever.

        Sent on the transition INTO first place rather than on a timer, so she
        gets the heads-up while she is still browsing the racks.
        """
        first = self.sudo().search([('state', '=', 'waiting')], order='create_date asc', limit=1)
        if first and not first.next_notified_at:
            first._send(_("You're next — we'll be with you in a moment."))
            first.next_notified_at = fields.Datetime.now()

    def modryn_call(self, employee=None):
        """Her turn. The SMS names the stylist so she knows who to look for."""
        self.ensure_one()
        values = {'state': 'called'}
        if employee:
            values['modryn_employee_id'] = employee.id
        self.write(values)
        if not self.turn_notified_at:
            stylist = employee or self.modryn_employee_id
            if stylist:
                body = _("We're ready for you — %(stylist)s is waiting.") % {
                    'stylist': stylist.name}
            else:
                body = _("We're ready for you — please come to the desk.")
            self._send(body)
            self.turn_notified_at = fields.Datetime.now()
        # Serving one customer promotes the next, so her heads-up goes out now.
        self._notify_next_in_line()
        return self

    def action_call_next(self):
        for entry in self:
            entry.modryn_call()

    def action_done(self):
        self.write({'state': 'done'})
        self._notify_next_in_line()

    # -------------------------------------------------------------------- cron
    @api.model
    def _modryn_expire_open_tickets(self):
        """Close the floor: nobody should wake up tomorrow still 'waiting'.

        Runs after closing time; anything still open becomes expired, and her
        page turns into a warm invitation to book instead.
        """
        stale = self.sudo().search([('state', 'in', OPEN_STATES)])
        if stale:
            stale.write({'state': 'expired'})
