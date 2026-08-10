from odoo import api, fields, models


class CalendarEvent(models.Model):
    """Who is serving this appointment: one accountable primary, any helpers.

    The bridal-floor reality is a saleswoman styling while a seamstress pins —
    one card, several people. The primary is the accountable one; helpers come
    and go without changing who owns the customer.
    """

    _inherit = 'calendar.event'

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )
    modryn_helper_ids = fields.Many2many(
        'hr.employee',
        'modryn_event_helper_rel',
        'event_id',
        'employee_id',
        string="Helpers",
    )

    ASSIGNMENT_FIELDS = ('modryn_employee_id', 'modryn_helper_ids')

    def write(self, vals):
        result = super().write(vals)
        # Assignment changes must reach every open floor board. The queue model
        # already owns the bus channel; bookings piggyback on the same one so
        # there is exactly one realtime mechanism to reason about.
        if any(f in vals for f in self.ASSIGNMENT_FIELDS):
            bookings = self.filtered('modryn_is_booking')
            if bookings:
                self.env['bus.bus']._sendone(
                    'modryn_queue', 'modryn_queue/update',
                    {'kind': 'booking_assignment', 'ids': bookings.ids},
                )
        return result


class ModrynQueueEntry(models.Model):
    """Who is serving this walk-in: same primary + helpers shape as bookings."""

    _inherit = 'modryn.queue.entry'

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )
    modryn_helper_ids = fields.Many2many(
        'hr.employee',
        'modryn_queue_helper_rel',
        'entry_id',
        'employee_id',
        string="Helpers",
    )

    def _payload(self):
        # Extend the existing bus payload so helper changes reach every open
        # board through the push modryn_queue_poc already wired up.
        payload = super()._payload()
        payload['employee_id'] = self.modryn_employee_id.id or False
        payload['employee_name'] = self.modryn_employee_id.name or ''
        payload['helper_ids'] = self.modryn_helper_ids.ids
        payload['helper_names'] = self.modryn_helper_ids.mapped('name')
        return payload

    @api.model
    def _assignment_changed_fields(self):
        return {'modryn_employee_id', 'modryn_helper_ids'}

    def write(self, vals):
        result = super().write(vals)
        # The base model only notifies on state changes; assignment changes are
        # just as visible on the board, so they push too.
        if self._assignment_changed_fields() & set(vals):
            self._notify_board()
        return result

    def modryn_assign(self, employee):
        """Dispatch this walk-in to a member of staff (legacy single-assign).

        Kept because the click-fallback and older callers use it; it now means
        "make her the primary and call the customer".
        """
        self.ensure_one()
        self.write({
            'modryn_employee_id': employee.id,
            'state': 'called',
        })
        return self
