from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynAvailability(models.Model):
    """"I can work that one." One row per employee per shift.

    Deliberately separate from the slot's employee_ids: what she OFFERED and
    what the manager ROSTERED are different facts, and collapsing them would
    mean ticking a box put you on the rota.
    """

    _name = 'modryn.availability'
    _description = 'Availability for a shift'
    _order = 'slot_id, employee_id'

    slot_id = fields.Many2one(
        'modryn.shift.slot', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(
        'hr.employee', required=True, ondelete='cascade', index=True)

    _slot_employee_uniq = models.Constraint(
        'unique(slot_id, employee_id)', "She has already offered to work that shift.")

    @api.constrains('slot_id')
    def _check_not_published(self):
        for row in self:
            if row.slot_id.published:
                raise ValidationError(_(
                    "That week is already published — ask your manager to change it."))

    @api.model
    def modryn_toggle(self, employee, slot):
        """Offer a shift, or withdraw the offer. Idempotent either way."""
        if slot.published:
            return False, _("That week is already published — ask your manager to change it.")
        existing = self.sudo().search([
            ('slot_id', '=', slot.id), ('employee_id', '=', employee.id)], limit=1)
        if existing:
            existing.unlink()
            return True, None
        self.sudo().create({'slot_id': slot.id, 'employee_id': employee.id})
        return True, None
