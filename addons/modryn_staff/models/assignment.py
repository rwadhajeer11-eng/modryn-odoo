from odoo import fields, models


class CalendarEvent(models.Model):
    """Who is actually serving this appointment."""

    _inherit = 'calendar.event'

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )


class ModrynQueueEntry(models.Model):
    """Who is actually serving this walk-in."""

    _inherit = 'modryn.queue.entry'

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )

    def _payload(self):
        # Extend the existing bus payload so an assignment change reaches every
        # open board through the push that modryn_queue_poc already wired up —
        # no second realtime mechanism.
        payload = super()._payload()
        payload['employee_id'] = self.modryn_employee_id.id or False
        payload['employee_name'] = self.modryn_employee_id.name or ''
        return payload

    def modryn_assign(self, employee):
        """Dispatch this walk-in to a member of staff.

        Assigning implies calling her over, so state moves with it: that is what
        makes the assignee count as occupied without anyone touching a status
        field.
        """
        self.ensure_one()
        self.write({
            'modryn_employee_id': employee.id,
            'state': 'called',
        })
        return self
