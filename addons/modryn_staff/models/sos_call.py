from datetime import datetime, timedelta

from odoo import _, api, fields, models

# A colleague who has not even acknowledged in half a minute is, on a busy
# Thursday, not coming. The managers hear about it.
ESCALATE_AFTER_SECONDS = 30


class ModrynSosCall(models.Model):
    """A member of staff asking for help, right now, from the floor.

    The point of this model is the CONTEXT it carries. "Dana needs help" makes a
    colleague cross the boutique asking questions; "Dana needs help with Michal
    in Room 2" sends her straight there with the zip already in mind.
    """

    _name = 'modryn.sos.call'
    _description = 'Call for help on the floor'
    _order = 'create_date desc, id desc'

    caller_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    # Empty means "any manager" — the general bell, not a specific colleague.
    target_id = fields.Many2one('hr.employee', ondelete='cascade', index=True)
    entry_id = fields.Many2one('modryn.queue.entry', ondelete='cascade')
    event_id = fields.Many2one('calendar.event', ondelete='cascade')
    room_id = fields.Many2one('modryn.fitting.room', ondelete='set null')
    note = fields.Char()
    state = fields.Selection(
        selection=[
            ('open', 'Open'),
            ('acked', 'On the way'),
            ('resolved', 'Resolved'),
        ],
        default='open',
        required=True,
        index=True,
    )
    acked_by_id = fields.Many2one('hr.employee', ondelete='set null')
    escalated_at = fields.Datetime()

    # ---------------------------------------------------------------- context
    def _customer_name(self):
        self.ensure_one()
        if self.entry_id:
            return self.entry_id.name
        if self.event_id:
            return self.event_id.name
        return ''

    def _where(self):
        """Where to go. The room when one is assigned, the customer otherwise.

        A boutique that has not set up rooms is not broken — it just gets the
        customer's name, which is what staff shout across the floor anyway.
        """
        self.ensure_one()
        if self.room_id:
            return self.room_id.name
        customer = self._customer_name()
        return _("with %s", customer) if customer else ''

    def _row(self):
        self.ensure_one()
        return {
            'id': self.id,
            'caller_id': self.caller_id.id,
            'caller_name': self.caller_id.name,
            'target_id': self.target_id.id or False,
            'target_name': self.target_id.name or '',
            'customer': self._customer_name(),
            'room': self.room_id.name or '',
            'where': self._where(),
            'note': self.note or '',
            'state': self.state,
            'escalated': bool(self.escalated_at),
        }

    # ----------------------------------------------------------------- notify
    def _notify(self):
        """Push to every open board; each one decides if it is being called."""
        for call in self:
            self.env['bus.bus']._sendone(
                'modryn_queue', 'modryn_queue/update',
                {'kind': 'sos', 'call': call._row()},
            )

    @api.model_create_multi
    def create(self, vals_list):
        calls = super().create(vals_list)
        calls._notify()
        return calls

    def write(self, vals):
        result = super().write(vals)
        if {'state', 'escalated_at', 'target_id'} & set(vals):
            self._notify()
        return result

    # ---------------------------------------------------------------- raising
    @api.model
    def modryn_raise(self, caller, target=None, record=None, note=None):
        """Ask for help, reusing an open call rather than stacking duplicates.

        Someone jabbing the button three times because nobody came must not
        produce three overlays — it is the same cry for help.
        """
        entry = record if record is not None and record._name == 'modryn.queue.entry' else None
        event = record if record is not None and record._name == 'calendar.event' else None
        domain = [
            ('caller_id', '=', caller.id),
            ('state', 'in', ('open', 'acked')),
            ('entry_id', '=', entry.id if entry else False),
            ('event_id', '=', event.id if event else False),
        ]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            return existing
        return self.sudo().create({
            'caller_id': caller.id,
            'target_id': target.id if target else False,
            'entry_id': entry.id if entry else False,
            'event_id': event.id if event else False,
            'room_id': (record.modryn_room_id.id
                        if record is not None and record.modryn_room_id else False),
            'note': (note or '').strip() or False,
        })

    def modryn_ack(self, employee):
        """Someone is on the way. Idempotent: the first responder wins."""
        self.ensure_one()
        if self.state != 'open':
            return self
        self.write({'state': 'acked', 'acked_by_id': employee.id if employee else False})
        return self

    def modryn_resolve(self):
        self.ensure_one()
        if self.state != 'resolved':
            self.write({'state': 'resolved'})
        return self

    # ------------------------------------------------------------- escalation
    @api.model
    def _modryn_escalate_unanswered(self):
        """Nobody acknowledged — make it every manager's problem.

        Only calls aimed at a NAMED colleague escalate. A call already addressed
        to the managers has nowhere left to go, and re-notifying it every minute
        would train them to ignore the overlay.
        """
        cutoff = datetime.utcnow() - timedelta(seconds=ESCALATE_AFTER_SECONDS)
        stale = self.sudo().search([
            ('state', '=', 'open'),
            ('escalated_at', '=', False),
            ('target_id', '!=', False),
            ('create_date', '<=', cutoff),
        ])
        # Clearing the target is what broadcasts it: every manager's board then
        # matches the call, and the original colleague still sees it too.
        stale.write({'escalated_at': fields.Datetime.now(), 'target_id': False})
