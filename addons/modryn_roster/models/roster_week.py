from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# The boutique's clock. Same zone the rest of the project pins, and for the same
# reason: a submission window is a wall-clock promise to the team ("it shuts
# Saturday night"), and UTC drifts an hour away from that for half the year.
TZ = pytz.timezone('Asia/Jerusalem')

# When the window opens and shuts unless a manager says otherwise, as
# (Python weekday, hour). Thursday 09:00 -> Saturday 21:00: the team gets the
# whole weekend to answer, and the manager has Sunday morning to build the week.
# Stored as a default rather than as rows, so a boutique that never touches it
# has nothing to maintain and no week can be missed by forgetting to open it.
DEFAULT_OPEN = (3, 9.0)    # Thursday 09:00
DEFAULT_CLOSE = (5, 21.0)  # Saturday 21:00

# Config keys, so an owner can move the recurring window once instead of per week.
PARAM_OPEN = 'modryn.roster.window_open'
PARAM_CLOSE = 'modryn.roster.window_close'


def _to_utc(day, hour):
    """A local wall-clock float hour on `day`, as the naive UTC a Datetime holds.

    Localize then convert — Israel observes DST, so a fixed offset is an hour
    wrong for half the year, including on the transition day itself. Same shape
    as modryn_booking's _utc_on, kept here rather than imported because
    modryn_roster must not start depending on modryn_booking's controllers.
    """
    whole = int(hour)
    naive = datetime.combine(day, datetime.min.time()).replace(
        hour=whole, minute=int(round((hour - whole) * 60)))
    return TZ.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)


def _from_utc(value):
    """Naive UTC -> the boutique's wall clock, for PRINTING only.

    Its absence was a live bug, not a theoretical one: _window() returns naive
    UTC and the page strftime'd it raw, so the shipped Saturday 21:00 deadline
    printed as "18:00". Harmless while nothing wrote the window - and fatal the
    moment a manager types 21:00 and the page echoes 18:00 back at her, because
    the only sane reading of that is "it did not save".
    """
    return pytz.UTC.localize(value).astimezone(TZ)


def _parse_window(raw, fallback):
    """"3:9.0" -> (3, 9.0). The fallback on anything unparseable.

    An owner editing this by hand in Settings is a real path, and a typo there
    must not take the roster down — it falls back to the shipped default and the
    week still opens.
    """
    try:
        weekday, hour = str(raw).split(':')
        weekday, hour = int(weekday), float(hour)
    except (AttributeError, TypeError, ValueError):
        return fallback
    if not (0 <= weekday <= 6) or not (0 <= hour <= 24):
        return fallback
    return weekday, hour


def window_days():
    """The weekday picker for the submission window, in RUNWAY order.

    Monday first, Sunday last - NOT the Sunday-first order the rest of the
    product uses. The window is anchored to the Monday of the week BEFORE the
    one being planned (see _window), so weekday 6 (Sunday) lands on week_start
    ITSELF - chronologically the last option, not the first. Showing it at the
    top of a list a manager reads as a timeline is how she picks the one day
    that opens submissions after the week has already begun.

    Labels come from weekday_selection() so they cannot drift from the model's
    own, and are translated there.
    """
    from .shift_template import weekday_selection
    labels = dict(weekday_selection())
    return [(str(wd), labels[str(wd)]) for wd in range(7)]


class ModrynRosterWeek(models.Model):
    """One planning week, and when its team may answer for it.

    Availability used to be answerable at any moment right up until the manager
    published, which is two problems wearing one coat: the team had no deadline,
    and the manager had no moment where the answers were final enough to build
    from — somebody could withdraw a shift she had already been counted into.

    A week is created ON DEMAND rather than by a cron. A cron that has to run
    every week to open a window is a cron that eventually does not run, and the
    failure is silent: the team simply cannot answer and nobody is told why.
    Reading the window computes it instead, so it is right even for a week
    nothing has touched yet.
    """

    _name = 'modryn.roster.week'
    _description = 'A planning week'
    _order = 'week_start desc'

    week_start = fields.Date(required=True, index=True)
    # Null means "use the recurring default". Set means a manager moved THIS
    # week — a holiday, a team away day. Nullable rather than pre-filled, so the
    # default can be changed later and every untouched week follows it.
    opens_at = fields.Datetime(string="Submissions open")
    closes_at = fields.Datetime(string="Submissions close")
    # Which shift types are not running this week at all: "no night shifts over
    # the holiday". Comma-separated codes rather than a relation, because the
    # set is three fixed strings that live in the field definition, and a
    # relation would need a model, a security row and a data file to express
    # what a Char already says.
    blocked_types = fields.Char(default='')

    _week_uniq = models.Constraint('unique(week_start)',
                                   "That planning week already exists.")

    # ------------------------------------------------------------------ window
    @api.model
    def _default_window(self):
        """(open_weekday, open_hour), (close_weekday, close_hour) for this shop."""
        Param = self.env['ir.config_parameter'].sudo()
        return (_parse_window(Param.get_param(PARAM_OPEN), DEFAULT_OPEN),
                _parse_window(Param.get_param(PARAM_CLOSE), DEFAULT_CLOSE))

    def _window(self):
        """This week's real open/close as naive UTC datetimes.

        The recurring default is anchored to the week BEFORE the one being
        planned — you answer for next week during this one — so both edges are
        computed backwards from week_start rather than forwards.
        """
        self.ensure_one()
        if self.opens_at and self.closes_at:
            return self.opens_at, self.closes_at
        (open_wd, open_h), (close_wd, close_h) = self._default_window()
        # week_start is a SUNDAY — the Israeli retail week — and the offsets are
        # Python weekdays counting from Monday, so both edges are measured from
        # the Monday of the week before.
        #
        # That Monday is SIX days back, not eight. Eight lands on the Saturday
        # before it, which quietly moved the whole window two days early: the
        # Thursday default opened on a Tuesday. Checked against a real date —
        # Sunday 2026-08-30 minus 6 is Monday 2026-08-24, and +3 from there is
        # Thursday 2026-08-27.
        previous_monday = self.week_start - timedelta(days=6)
        opens = self.opens_at or _to_utc(previous_monday + timedelta(days=open_wd), open_h)
        closes = self.closes_at or _to_utc(previous_monday + timedelta(days=close_wd), close_h)
        return opens, closes

    def modryn_is_open(self):
        """May the team still answer for this week?"""
        self.ensure_one()
        opens, closes = self._window()
        now = fields.Datetime.now()
        return opens <= now < closes

    @api.model
    def modryn_is_frozen(self, week_start):
        """Has this week been published?

        DERIVED from the slots rather than stored as a second flag. Publishing
        is already week-wide - the controller publishes every slot sharing a
        week_start - so this reads the existing truth instead of duplicating it.
        A second stored flag would need backfilling on upgrade day, and getting
        that backfill wrong would silently UNFREEZE every week already
        published.
        """
        return bool(self.env['modryn.shift.slot'].sudo().search_count([
            ('week_start', '=', week_start), ('published', '=', True)]))

    @api.model
    def modryn_set_default_window(self, open_wd, open_hour, close_wd, close_hour):
        """Move the RECURRING window, and prove it landed.

        Written through the model and not from the controller, for the reason
        modryn_set_blocked already documents: a stored junk value here is
        INVISIBLE, because _parse_window is deliberately forgiving and answers
        anything unparseable with the shipped default. An owner would then be
        shown Thursday 09:00, read it as "my save did not take", and try again.

        So: a fixed format on the way in, and a read-back through the same
        parser on the way out. The caller compares.
        """
        valid = {code for code, _label in window_days()}
        if str(open_wd) not in valid or str(close_wd) not in valid:
            return None
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param(PARAM_OPEN, '%s:%.2f' % (int(open_wd), float(open_hour)))
        Param.set_param(PARAM_CLOSE, '%s:%.2f' % (int(close_wd), float(close_hour)))
        return self._default_window()

    @api.constrains('opens_at', 'closes_at')
    def _check_window(self):
        for week in self:
            if week.opens_at and week.closes_at and week.closes_at <= week.opens_at:
                raise ValidationError(
                    _("Submissions have to close after they open."))

    # ------------------------------------------------------------ shift types
    def modryn_blocked_types(self):
        self.ensure_one()
        return [code for code in (self.blocked_types or '').split(',') if code]

    def modryn_set_blocked(self, codes):
        """Which shift types are off this week. Replace-set, never a toggle.

        A toggle needs the caller to know the current value, and two managers on
        two phones would then each toggle from a different one.

        Filtered against the shift_type selection HERE and not only in the
        controller. The controller is one caller; a stored junk code would
        block nothing and be invisible, because a type that does not exist can
        never match a slot.
        """
        self.ensure_one()
        valid = {code for code, _label in
                 self.env['modryn.shift.template']._fields['shift_type'].get_description(
                     self.env)['selection']}
        self.blocked_types = ','.join(sorted({c for c in codes if c in valid}))
        return self.modryn_blocked_types()

    # ----------------------------------------------------------------- lookup
    @api.model
    def modryn_for(self, week_start):
        """The row for that week, created if this is the first time anyone asked."""
        week = self.sudo().search([('week_start', '=', week_start)], limit=1)
        if not week:
            week = self.sudo().create({'week_start': week_start})
        return week


class ModrynRosterSubmission(models.Model):
    """One team member's answer for one week: her note, and the moment she sent it.

    Separate from modryn.availability, which stays per SLOT. This row is the act
    of sending — the thing that turns a set of ticks into an answer the manager
    can build on. Without it "she has not answered yet" and "she can work
    nothing that week" are the same empty set, and the manager cannot tell the
    difference between a team member who is unavailable and one who is on
    holiday and has not looked.
    """

    _name = 'modryn.roster.submission'
    _description = 'A week of availability, as sent'
    _order = 'submitted_at desc'

    week_start = fields.Date(required=True, index=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True,
                                  ondelete='cascade')
    note = fields.Text(string="Anything we should know")
    submitted_at = fields.Datetime(readonly=True)

    _week_employee_uniq = models.Constraint(
        'unique(week_start, employee_id)',
        "That person has already answered for that week.")

    @api.model
    def modryn_for(self, week_start, employee):
        row = self.sudo().search([('week_start', '=', week_start),
                                  ('employee_id', '=', employee.id)], limit=1)
        if not row:
            row = self.sudo().create({'week_start': week_start,
                                      'employee_id': employee.id})
        return row

    def modryn_send(self, note=None):
        """Stamp it as sent. Re-sending is allowed while the window is open."""
        self.ensure_one()
        values = {'submitted_at': fields.Datetime.now()}
        if note is not None:
            values['note'] = note
        self.write(values)
        return True
