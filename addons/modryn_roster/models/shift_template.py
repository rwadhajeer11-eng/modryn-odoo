from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Python's weekday(): Mon=0 … Sun=6. The Israeli retail week runs Sunday first,
# so the grid orders by this list rather than by the raw number.
WEEKDAY_ORDER = [6, 0, 1, 2, 3, 4, 5]


def weekday_selection():
    return [
        ('6', _("Sunday")),
        ('0', _("Monday")),
        ('1', _("Tuesday")),
        ('2', _("Wednesday")),
        ('3', _("Thursday")),
        ('4', _("Friday")),
        ('5', _("Saturday")),
    ]


class ModrynShiftTemplate(models.Model):
    """A shift this boutique actually runs — "Thursday late", "Sunday morning".

    Owner data, like roles, pieces and rooms. A boutique that opens late on
    Thursdays and runs a Saturday-night bridal evening must be able to say so
    without a developer, and no two boutiques keep the same hours.
    """

    _name = 'modryn.shift.template'
    _description = 'Shift template'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    weekday = fields.Selection(selection=weekday_selection, required=True, default='6')
    start_hour = fields.Float(default=10.0, required=True)
    end_hour = fields.Float(default=18.0, required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    target_ids = fields.One2many('modryn.shift.target', 'template_id')

    _name_weekday_uniq = models.Constraint(
        'unique(name, weekday)', "That shift already exists on that day.")

    @api.constrains('start_hour', 'end_hour')
    def _check_hours(self):
        for template in self:
            if not 0 <= template.start_hour < 24 or not 0 < template.end_hour <= 24:
                raise ValidationError(_("Shift hours must be within a single day."))
            if template.end_hour <= template.start_hour:
                raise ValidationError(_("A shift has to end after it starts."))

    def _label(self):
        self.ensure_one()
        return '%s · %s–%s' % (self.name, _fmt(self.start_hour), _fmt(self.end_hour))


def _fmt(value):
    """A float hour as a wall clock, which is how staff read a rota."""
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    return '%02d:%02d' % (hours, minutes)


class ModrynShiftTarget(models.Model):
    """How many of a given role this shift needs.

    Per ROLE, not just a head count: two saleswomen and no seamstress is not the
    same shift as one of each, even though both are "two people".
    """

    _name = 'modryn.shift.target'
    _description = 'Coverage target for a shift'
    _order = 'template_id, role_id'

    template_id = fields.Many2one(
        'modryn.shift.template', required=True, ondelete='cascade', index=True)
    role_id = fields.Many2one(
        'modryn.staff.role', required=True, ondelete='cascade', index=True)
    required = fields.Integer(default=1, required=True)

    _template_role_uniq = models.Constraint(
        'unique(template_id, role_id)', "That role already has a target on this shift.")

    @api.constrains('required')
    def _check_required(self):
        for target in self:
            if target.required < 0:
                raise ValidationError(_("A target cannot be negative."))
