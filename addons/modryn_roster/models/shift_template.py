from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Python's weekday(): Mon=0 … Sun=6. The Israeli retail week runs Sunday first,
# so the grid orders by this list rather than by the raw number.
WEEKDAY_ORDER = [6, 0, 1, 2, 3, 4, 5]


# The three parts of a retail day, in the order they happen. Codes are words and
# not digits (unlike PRIORITIES below): nothing sorts by them — the grid orders
# by this list — and 'night' reads in a log where '2' does not.
SHIFT_TYPE_ORDER = ['morning', 'middle', 'night']

# Where a template lands when nobody has said. Boundaries are LOCAL wall-clock
# start hours: before 12:00 is a morning, 17:00 and later is a night, the rest
# is the middle. Used only to place templates that predate the field — after
# that the owner's own choice is what counts.
TYPE_BY_START_HOUR = ((12.0, 'morning'), (17.0, 'middle'), (24.1, 'night'))


def shift_type_selection():
    return [
        ('morning', _("Morning")),
        ('middle', _("Midday")),
        ('night', _("Evening")),
    ]


# Which parts of the day the boutique runs, as a config parameter: one value
# for the whole tenant, and this product is one database per boutique.
PARTS_PARAM = 'modryn.roster.parts'


def active_shift_types(env):
    """The parts this boutique runs, in the usual order.

    Falls back to ALL THREE when nothing has been set, which is what every
    existing boutique has - the setting arrives after they were already
    answering for three parts, and a default of "none" would empty their grid on
    upgrade.

    Unknown codes in the stored value are dropped rather than trusted: the
    parameter is writable from the back office, and a typo there should cost a
    part rather than a traceback on the page every woman opens.
    """
    raw = env['ir.config_parameter'].sudo().get_param(PARTS_PARAM, '')
    chosen = [c.strip() for c in raw.split(',') if c.strip()]
    kept = [c for c in SHIFT_TYPE_ORDER if c in chosen]
    return kept or list(SHIFT_TYPE_ORDER)


def set_active_shift_types(env, codes):
    """Store the parts. Returns the list actually kept.

    Refuses to store nothing: zero parts is a grid with no rows, which reads as
    the page being broken rather than as the boutique being configured.
    """
    kept = [c for c in SHIFT_TYPE_ORDER if c in set(codes or [])]
    if not kept:
        return active_shift_types(env)
    env['ir.config_parameter'].sudo().set_param(PARTS_PARAM, ','.join(kept))
    return kept


def type_for_hour(start_hour):
    """Which part of the day a shift starting at `start_hour` belongs to."""
    for boundary, code in TYPE_BY_START_HOUR:
        if start_hour < boundary:
            return code
    return 'night'


def weekday_name(day):
    """The name of a date's weekday, in the READER's language.

    date.strftime('%A') cannot do this. It formats through the C locale, which
    on this server is English and stays English no matter who is looking - so
    the roster table printed "Monday" down its header on a Hebrew-first page,
    and the screen readers' cell labels came out half Hebrew, half English
    ("בוקר Monday 31.08"). Nothing errored; it simply was not translated, which
    is the failure mode that survives a review.

    weekday_selection() is already the translated list and is already the source
    the shift forms render from, so reading it here keeps one set of day names
    in the product instead of two that can disagree.
    """
    return dict(weekday_selection()).get(str(day.weekday()), '')


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
    # Morning / middle / night, so the week reads as a 7x3 grid instead of a
    # flat list of whatever the owner happened to name her shifts. The NAME
    # stays free — "Thursday late" is still what the boutique calls it — but the
    # type is what the grid groups by and what a manager switches off wholesale
    # ("no night shifts over the holiday").
    #
    # Defaulted rather than required: every template that existed before this
    # field needs an answer, and 'morning' is the one that leaves a shop opening
    # at 10:00 described correctly. _compute_default_type below moves the
    # obvious ones on upgrade so the default is not silently wrong for evenings.
    shift_type = fields.Selection(
        selection=lambda self: shift_type_selection(),
        required=True, default='morning', index=True)
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
