from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# One slot length for the whole boutique, deliberately not a field. Per-
# appointment-type DURATION is still a later feature and stays out on purpose:
# a 90-minute fitting overlaps the next hour, and no unique index on a start
# time can express that — it needs a tstzrange EXCLUDE constraint, btree_gist,
# and a grid that is no longer uniform. Half of that ships a page that offers
# 11:00 while a 90-minute fitting is running through it. Capacity is the part
# the index can actually enforce, so capacity is the part that shipped.
SLOT_MINUTES = 60

# The lattice that used to be a constant in two controllers, reproduced exactly
# so that turning it into data changes nothing anybody can see. Friday ('4') and
# Saturday ('5') get NO row: an absent weekday is how a closed day is spelt.
DEFAULT_HOURS = [
    ('6', 10.0, 18.0),
    ('0', 10.0, 18.0),
    ('1', 10.0, 18.0),
    ('2', 10.0, 18.0),
    ('3', 10.0, 18.0),
]


def _starts(start_hour, end_hour):
    """Slot start hours inside one window.

    A fitting must FINISH by closing time, so a window that shuts at 18:00
    never offers 18:00. The epsilon covers windows whose length is not a whole
    number of slots in binary floating point.
    """
    step = SLOT_MINUTES / 60.0
    starts = []
    hour = start_hour
    while hour + step <= end_hour + 1e-9:
        starts.append(round(hour, 4))
        hour += step
    return starts


def weekday_selection(model=None):
    """Python's weekday() values as strings — Mon=0 … Sun=6.

    Takes the recordset Odoo hands a callable selection. `determine()`
    (odoo/orm/fields.py) calls `needle(records)`, so a zero-argument version
    raises TypeError the moment anything asks for the field's description —
    fields_get(), an export, or a web client reading the form. The write path
    never notices, because convert_to_cache short-circuits while `_selection`
    is None, which is why the identical helper in modryn_roster has survived
    without a parameter.

    The same encoding and the same Sunday-first order as modryn.shift.template,
    because the owner reads the two grids side by side and a boutique that opens
    late on Thursday says so in both. Duplicated rather than imported: the roster
    depends on this module, so importing it back would be a load cycle.
    """
    return [
        ('6', _("Sunday")),
        ('0', _("Monday")),
        ('1', _("Tuesday")),
        ('2', _("Wednesday")),
        ('3', _("Thursday")),
        ('4', _("Friday")),
        ('5', _("Saturday")),
    ]


class ModrynOpeningHours(models.Model):
    """When this boutique is open — the grid /book offers its slots from.

    Sunday–Thursday 10:00–18:00 was hardcoded twice, and the second copy had
    lost the weekday half entirely, so a waitlist claim link could offer a
    Friday. Owner data now, like roles, rooms and shifts: no two boutiques keep
    the same hours and none of them should need a developer to change them.

    Several rows per weekday are allowed on purpose — a shop that shuts for the
    afternoon and reopens at 16:00 is an ordinary Israeli retail week, not an
    edge case.
    """

    _name = 'modryn.opening.hours'
    _description = 'Opening hours'
    _order = 'weekday, start_hour'

    weekday = fields.Selection(selection=weekday_selection, required=True, default='6')
    start_hour = fields.Float(default=10.0, required=True)
    end_hour = fields.Float(default=18.0, required=True)
    # Capacity belongs to the WINDOW rather than to a new appointment-type model
    # because that is how a boutique already says it: "Thursday evening we can
    # take two". It also leaves room for min(capacity, rostered stylists) later
    # without another table.
    capacity = fields.Integer(
        string="Fittings at once",
        default=1,
        required=True,
        help="How many customers this boutique can take at the same time in this window.",
    )
    active = fields.Boolean(default=True)

    # An archived window keeps its start time reserved, so switching a window
    # back on is a restore, never a re-create.
    _weekday_start_uniq = models.Constraint(
        'unique(weekday, start_hour)',
        "That day already has a window starting at that time.")

    @api.constrains('start_hour', 'end_hour')
    def _check_hours(self):
        for window in self:
            if not 0 <= window.start_hour < 24 or not 0 < window.end_hour <= 24:
                raise ValidationError(_("Opening hours must be within a single day."))
            if window.end_hour <= window.start_hour:
                raise ValidationError(_("A boutique has to close after it opens."))

    @api.constrains('capacity')
    def _check_capacity(self):
        for window in self:
            if window.capacity < 1:
                # Zero is not "open but unbookable" — that is what archiving the
                # window or adding a closure says, and both of those stay visible
                # as a decision. A zero here would read as an open shop that
                # refuses every hour for no stated reason.
                raise ValidationError(
                    _("An open window has to take at least one fitting."))

    def _capacities(self, domain):
        """{weekday_str: {hour_float: capacity_int}} for the matching windows.

        The one place the window arithmetic happens; the three public methods
        below are the shapes their callers want, not three copies of this.

        sudo() because the public /book page reads this as the anonymous website
        user: opening hours are printed on the shop door, there is nothing here
        to leak, and the alternative is a 500 on the storefront.
        """
        by_day = {}
        # search() drops archived rows by itself (active_test), which is how an
        # owner switches a window off without losing it.
        for window in self.sudo().search(domain):
            hours = by_day.setdefault(window.weekday, {})
            for hour in _starts(window.start_hour, window.end_hour):
                # Overlapping windows on one weekday: the roomier one wins.
                # max() rather than last-write, so the answer does not depend on
                # _order — and so an hour is never offered twice.
                hours[hour] = max(hours.get(hour, 0), window.capacity)
        # Sorted keys, because callers render these in order and several of them
        # iterate the dict directly rather than sorting it themselves.
        return {day: dict(sorted(hours.items())) for day, hours in by_day.items()}

    @api.model
    def modryn_hours_on(self, day_date):
        """The local wall-clock hours a fitting may START at on that date.

        An empty list means shut, and that empty list IS the weekday filter — a
        caller looping over this cannot offer a Friday by forgetting to check.
        """
        return sorted(self.modryn_capacities_on(day_date))

    @api.model
    def modryn_capacities_on(self, day_date):
        """{hour_float: capacity_int} for that date. Empty dict means shut."""
        weekday = str(day_date.weekday())
        return self._capacities([('weekday', '=', weekday)]).get(weekday, {})

    @api.model
    def modryn_hours_by_weekday(self):
        """Every weekday's slot starts AND capacities, in ONE read.

        Keys are weekday strings; each value is {hour: capacity}. Iterating a
        value yields the hours in order, which is what it yielded when this
        returned plain lists.

        /book renders a fortnight, so asking modryn_capacities_on() per day
        fired fourteen queries at a five-row table on the boutique's busiest
        public page — and three times that on a failed submit, which re-renders.
        The arithmetic is identical; only the number of round trips differs.
        """
        return self._capacities([])

    @api.model
    def modryn_hour_label(self, value):
        """A float hour as a wall clock, which is how anyone reads a time.

        Mirrors _fmt() in modryn_roster/models/shift_template.py; copied for the
        same reason weekday_selection() is.
        """
        hours = int(value)
        minutes = int(round((value - hours) * 60))
        return '%02d:%02d' % (hours, minutes)
