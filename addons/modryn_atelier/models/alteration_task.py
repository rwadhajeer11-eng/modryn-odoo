from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

STATES = [
    ('intake', "Intake"),
    ('in_progress', "In progress"),
    ('ready', "Ready"),
    ('delivered', "Delivered"),
]
OPEN_STATES = ('intake', 'in_progress', 'ready')


class ModrynAlterationTask(models.Model):
    """One piece of alteration work on one customer's garment."""

    _name = 'modryn.alteration.task'
    _description = 'Alteration task'
    _order = 'due_date asc, id asc'

    # Who it is for. Phone is denormalised on purpose: the workshop calls the
    # customer directly, and a partner record may be merged or renamed later.
    partner_id = fields.Many2one('res.partner', string="Customer", ondelete='set null')
    customer_name = fields.Char(required=True)
    customer_phone = fields.Char()

    # What is being altered. The variant carries the size, which is what the
    # seamstress actually needs off the rail.
    variant_id = fields.Many2one('product.product', string="Dress / size",
                                 ondelete='set null')
    piece_ids = fields.Many2many('modryn.garment.piece', string="Pieces")
    note = fields.Text(string="Instructions")

    seamstress_id = fields.Many2one('hr.employee', string="Seamstress",
                                    index=True, ondelete='set null')
    state = fields.Selection(selection=STATES, default='intake', required=True, index=True)
    due_date = fields.Date(string="Due")
    delivered_at = fields.Datetime(readonly=True)

    # Computed, not stored: "overdue" changes as the clock moves, so a stored
    # flag would be wrong from the moment it was written until something
    # happened to rewrite it.
    is_overdue = fields.Boolean(compute='_compute_is_overdue')

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for task in self:
            task.is_overdue = bool(
                task.due_date and task.due_date < today and task.state in OPEN_STATES
            )

    @api.constrains('seamstress_id')
    def _check_seamstress(self):
        # A task may be unassigned (it sits in intake until someone picks it up),
        # but it must never be assigned to someone who has left.
        for task in self:
            if task.seamstress_id and not task.seamstress_id.active:
                raise ValidationError(_("That staff member is archived."))

    def action_advance(self, target):
        """Move a task forward. Only ever forward — history is not a toggle."""
        self.ensure_one()
        order = [s[0] for s in STATES]
        if target not in order:
            return False
        if order.index(target) <= order.index(self.state):
            return False
        values = {'state': target}
        if target == 'delivered':
            values['delivered_at'] = fields.Datetime.now()
        self.write(values)
        return True

    def _row(self):
        """Plain dict for a template or a JSON route.

        Portal users (managers, seamstresses) have no ORM access to these
        records; everything reaches them through controllers under sudo(), so
        nothing hands out a live recordset.
        """
        self.ensure_one()
        return {
            'id': self.id,
            'customer': self.customer_name,
            'phone': self.customer_phone or '',
            'dress': self.variant_id.product_tmpl_id.name if self.variant_id else '',
            'size': (self.variant_id.product_template_attribute_value_ids[:1].name
                     if self.variant_id else ''),
            'pieces': self.piece_ids.mapped('name'),
            'note': self.note or '',
            'seamstress_id': self.seamstress_id.id or False,
            'seamstress': self.seamstress_id.name or '',
            'state': self.state,
            'due': self.due_date.strftime('%d.%m.%Y') if self.due_date else '',
            'overdue': self.is_overdue,
        }
