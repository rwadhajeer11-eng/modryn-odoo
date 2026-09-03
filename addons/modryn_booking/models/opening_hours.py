from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Imported lazily inside the method that needs it would be tidier, but this
# module is imported before queue_hours in models/__init__.py and the constant
# is a plain int - see the note there for why it is 1 and not 0.
DEFAULT_PER_HOUR = 1

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
    # NO LONGER ASKED FOR ON ANY SCREEN. "How many at the same time" is a
    # question about one hour, not about a window, and it is asked on the
    # queue-hours grid now. The column stays because rows carry it and dropping
    # a column is a migration, not a screen change; nothing reads it.
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
        # THE GRID DECIDES, once she has said anything at all.
        #
        # It began as a modifier on the opening hours: the window said which
        # hours existed and the grid said how many each took. That could not
        # answer a shop that opens at eight in the evening, or one that works
        # Friday - the grid could only speak about hours the window already
        # offered, and it drew five days because five days had windows.
        #
        # So: a grid with ANY row in it is the whole answer, and the opening
        # hours go back to being what a customer reads on the door. A grid with
        # no rows at all falls back to the old behaviour - every open hour, one
        # each - so a boutique that never touches the screen keeps the week it
        # already had rather than waking up shut.
        grid = self.env['modryn.queue.hour'].modryn_grid()
        if grid:
            wanted = {}
            for weekday, hours in grid.items():
                live = {hour: how_many for hour, how_many in hours.items()
                        if how_many > 0}
                if live:
                    wanted[weekday] = dict(sorted(live.items()))
            # The domain still applies: modryn_capacities_on() asks for one
            # weekday and must not be handed the whole week.
            days = [term[2] for term in domain
                    if isinstance(term, (list, tuple)) and term[0] == 'weekday']
            if days:
                wanted = {day: hours for day, hours in wanted.items()
                          if day in days}
            return wanted

        by_day = {}
        # search() drops archived rows by itself (active_test), which is how an
        # owner switches a window off without losing it.
        for window in self.sudo().search(domain):
            hours = by_day.setdefault(window.weekday, {})
            for hour in _starts(window.start_hour, window.end_hour):
                # Overlapping windows on one weekday: the roomier one wins.
                # max() rather than last-write, so the answer does not depend on
                # _order — and so an hour is never offered twice.
                hours[hour] = max(hours.get(hour, 0), DEFAULT_PER_HOUR)
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
        """{hour_float: capacity_int} for that date. Empty dict means shut.

        THE DATE IS ASKED FIRST. A day the owner has written hours against
        answers for itself and the weekly pattern is not consulted at all —
        which is what makes "we are at a fair on the 14th" expressible without
        editing the week twice.

        Hours set to zero are dropped here rather than returned as zero, so
        every caller keeps its existing rule that a missing hour is an hour not
        offered. A date whose every hour is zero therefore comes back empty,
        and empty already means shut everywhere this is read.
        """
        named = self.env['modryn.queue.day'].modryn_on(day_date)
        if named:
            return {hour: how_many for hour, how_many in sorted(named.items())
                    if how_many > 0}
        weekday = str(day_date.weekday())
        return self._capacities([('weekday', '=', weekday)]).get(weekday, {})

    @api.model
    def modryn_week(self):
        """The week as a customer reads it: every day, open or shut.

        EVERY DAY, including the shut ones. A list that silently omits Friday
        leaves a bride counting the rows to work out whether Friday is missing
        or whether she misread - and "Friday: closed" is the answer she came
        for as much as any opening time is.

        Two windows on one day come back as two ranges on one line, because
        that is what a shop with a lunch break actually does.

        sudo() for the same reason the rest of this model uses it: opening hours
        are printed on the door, and the alternative is a 500 on the storefront.
        """
        by_day = {}
        for window in self.sudo().search([]):
            by_day.setdefault(window.weekday, []).append(
                (window.start_hour, window.end_hour))

        def clock(value):
            return '%02d:%02d' % (int(value), round((value % 1) * 60))

        week = []
        for code, label in weekday_selection(None):
            windows = sorted(by_day.get(code) or [])
            week.append({
                'code': code,
                'label': label,
                'closed': not windows,
                'ranges': ['%s–%s' % (clock(a), clock(b)) for a, b in windows],
            })
        return week

    @api.model
    def modryn_open_hours_by_weekday(self):
        """{weekday_str: [hour, ...]} — every hour the door is unlocked.

        IGNORES THE QUEUE GRID ON PURPOSE, which is the whole reason it is not
        modryn_hours_by_weekday. That one answers "what may a bride be offered",
        and an hour set to none online is correctly missing from it. This one
        answers "what hours exist to have an opinion about", which is what the
        manager's grid has to draw a row for — including the ones she has just
        set to none, or she could never set them back.
        """
        by_day = {}
        for window in self.sudo().search([]):
            hours = by_day.setdefault(window.weekday, set())
            hours.update(_starts(window.start_hour, window.end_hour))
        return {day: sorted(hours) for day, hours in by_day.items()}

    @api.model
    def modryn_capacities_over(self, dates):
        """{date: {hour: capacity}} for a run of dates, in TWO reads.

        The layered answer of modryn_capacities_on(), for a whole fortnight at
        once. /book renders fourteen days and calling the single-date version
        per day would put back exactly the round trips modryn_hours_by_weekday
        was written to remove — one query per day against a table with five
        rows in it, on the boutique's busiest public page.

        The arithmetic is identical to the single-date version and deliberately
        expressed in the same order: what she wrote against the DATE, then the
        week she normally works. Two callers means two places to keep true, so
        anything past the layering itself stays in _capacities().
        """
        if not dates:
            return {}
        named = self.env['modryn.queue.day'].modryn_days(min(dates), max(dates))
        by_weekday = self.modryn_hours_by_weekday()
        answer = {}
        for day in dates:
            hours = named.get(day)
            if hours:
                answer[day] = {hour: how_many
                               for hour, how_many in sorted(hours.items())
                               if how_many > 0}
            else:
                answer[day] = by_weekday.get(str(day.weekday()), {})
        return answer

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
    def modryn_daily_caps(self, days):
        """Per-date ceiling on concurrent fittings, or {} when nothing caps them.

        A window's capacity says what the ROOM can hold; it cannot know whether
        anyone is there to do the fitting. Anything that does know overrides this
        method — modryn_roster caps a date by the stylists its published rota
        puts on the floor — and this module stays ignorant of them, because the
        dependency only ever points this way.

        A date ABSENT from the mapping is uncapped and keeps its window capacity.
        Absence is the sentinel and 0 is NOT: an override that returns 0 for a
        date it knows nothing about closes the boutique's whole grid, silently,
        for every hour of that day.

        Takes the WHOLE list of dates and answers in one go, because /book
        renders a fortnight and the read behind it is a fixed number of queries
        by design. A per-day variant of this would put fourteen back.
        """
        return {}

    @api.model
    def modryn_hour_label(self, value):
        """A float hour as a wall clock, which is how anyone reads a time.

        Mirrors _fmt() in modryn_roster/models/shift_template.py; copied for the
        same reason weekday_selection() is.
        """
        hours = int(value)
        minutes = int(round((value - hours) * 60))
        return '%02d:%02d' % (hours, minutes)
