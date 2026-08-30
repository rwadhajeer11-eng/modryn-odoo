from datetime import datetime, time, timedelta

import psycopg2
import pytz

from odoo import api, fields, models

# The boutique's own clock. Every figure on the hours panel is worked out in it:
# a shift that runs to eleven at night is stored as 20:00 UTC, and grouping by
# the UTC date would move half the summer's evenings onto the following day.
TZ = pytz.timezone('Asia/Jerusalem')


# Under a minute on the floor is a mis-press, not a shift. One minute rather
# than a few seconds because the gesture it filters is human - open the page,
# read something, leave - and nobody works a fifty-second shift.
MIN_SPELL_SECONDS = 60


class ModrynShiftAttendance(models.Model):
    """One woman's day on the floor: when she came on, and when she went off.

    hr.employee.modryn_on_shift_since answers "is she here now" and nothing else
    - it is cleared when she leaves, so the moment she left is gone the instant
    it becomes interesting. The supervisor's screen asks exactly the question
    that field cannot answer, so the pair of times is recorded here instead.

    A ROW PER SHIFT rather than a running total on the employee: a woman comes
    on and off more than once in a day (a break, a shift she covers later), and
    a single pair of columns turns the second arrival into an overwrite of the
    first. It is also the only shape that can ever answer "was she here on the
    twelfth", which is the question that follows the first time somebody asks
    about a Saturday.

    Nothing prunes it. A row is one line of a boutique's own record of who was
    on its floor, and at a shift a day per person it is a few hundred rows a
    year - the cost of keeping it is nothing next to the cost of an argument
    about a Tuesday nobody can reconstruct.
    """

    _name = 'modryn.shift.attendance'
    _description = 'Time on the floor'
    _order = 'started_at desc, id desc'

    employee_id = fields.Many2one(
        'hr.employee', required=True, index=True, ondelete='cascade')
    started_at = fields.Datetime(
        string="Came on the floor", required=True, index=True)
    # Empty means she is still on the floor. Not defaulted to the start: a
    # zero-length shift and an open one would then look the same, and only one
    # of them is somebody standing in the room.
    ended_at = fields.Datetime(string="Went off the floor")

    # At most one OPEN row per woman, decided by the database rather than by a
    # search that hopes nothing happens between reading and writing. The page
    # that draws the board calls modryn_open on every load, so two tabs - or the
    # load test - can reach that gap. Closed rows are exempt: a day of coming
    # and going is many of them by design.
    _one_open_spell = models.UniqueIndex(
        "(employee_id) WHERE ended_at IS NULL",
        "That employee is already on the floor.",
    )

    # ------------------------------------------------------------- her hours
    @api.model
    def _modryn_local(self, value):
        """A stored naive-UTC datetime as the boutique's wall clock.

        Every figure below is worked out in LOCAL time. A shift that runs from
        half past nine at night to eleven is stored as 18:30-20:00 UTC, and
        grouping those by their UTC date puts half the summer's evenings on the
        following day.
        """
        return pytz.utc.localize(value).astimezone(TZ) if value else None

    @api.model
    def modryn_months(self, employee):
        """Every month she has any record in, newest first.

        Read off the rows rather than offered as a fixed range: a woman hired in
        March should not be given a dropdown of the year's other eleven months,
        each of which would open on an empty table and read as lost data.
        """
        if not employee:
            return []
        rows = self.sudo().search(
            [('employee_id', '=', employee.id)], order='started_at desc')
        seen = []
        for row in rows:
            local = self._modryn_local(row.started_at)
            key = (local.year, local.month)
            if key not in seen:
                seen.append(key)
        return seen

    @api.model
    def modryn_month(self, employee, year, month):
        """One month: a line per day she was here, and what it adds up to.

        A spell still open counts up to NOW and says so. The alternative is to
        skip it, which would show a woman standing on the floor a total that
        does not include the hours she is putting in as she reads it.
        """
        empty = {'days': [], 'day_count': 0, 'hours': 0.0, 'open': False}
        if not employee:
            return empty
        start = TZ.localize(datetime(year, month, 1))
        end = TZ.localize(datetime(year + (month // 12), (month % 12) + 1, 1))
        rows = self.sudo().search([
            ('employee_id', '=', employee.id),
            ('started_at', '>=', start.astimezone(pytz.utc).replace(tzinfo=None)),
            ('started_at', '<', end.astimezone(pytz.utc).replace(tzinfo=None)),
        ], order='started_at')

        now = fields.Datetime.now()
        by_day, order = {}, []
        still_open = False
        for row in rows:
            finish = row.ended_at
            if not finish:
                finish = now
                still_open = True
            seconds = max(0.0, (finish - row.started_at).total_seconds())
            local = self._modryn_local(row.started_at)
            key = local.date()
            if key not in by_day:
                by_day[key] = {'date': key, 'hours': 0.0, 'spells': [],
                               'open': False}
                order.append(key)
            bucket = by_day[key]
            bucket['hours'] += seconds / 3600.0
            bucket['open'] = bucket['open'] or not row.ended_at
            bucket['spells'].append({
                'in': local.strftime('%H:%M'),
                'out': (self._modryn_local(row.ended_at).strftime('%H:%M')
                        if row.ended_at else ''),
            })

        days = []
        for key in order:
            bucket = by_day[key]
            bucket['hours'] = round(bucket['hours'], 2)
            bucket['label'] = key.strftime('%d.%m.%Y')
            days.append(bucket)
        return {
            'days': days,
            'day_count': len(days),
            'hours': round(sum(d['hours'] for d in days), 2),
            'open': still_open,
        }

    @api.model
    def modryn_week_hours(self, employee):
        """This week so far, counted from SUNDAY.

        Sunday because that is the week this product already works in - the
        rota's grid, its submission windows and its publish all start there, and
        a second definition of "this week" on the profile page would disagree
        with the schedule she is looking at on the next tab.
        """
        if not employee:
            return 0.0
        today = datetime.now(TZ).date()
        sunday = today - timedelta(days=(today.weekday() + 1) % 7)
        start = TZ.localize(datetime.combine(sunday, time.min))
        rows = self.sudo().search([
            ('employee_id', '=', employee.id),
            ('started_at', '>=', start.astimezone(pytz.utc).replace(tzinfo=None)),
        ])
        now = fields.Datetime.now()
        total = sum(
            max(0.0, ((row.ended_at or now) - row.started_at).total_seconds())
            for row in rows)
        return round(total / 3600.0, 2)

    @api.model
    def modryn_open(self, employee, started_at=None):
        """She came on. Returns the row, or the one already open.

        Idempotent on purpose: the floor page can be opened twice, and a second
        row would put the same woman on the floor twice over on the supervisor's
        screen. Being safe to call again is also what lets the caller call it on
        EVERY press rather than only on the press that flips the flag - the
        version that guarded on the flag left anybody already mid-shift with no
        row at all, for good.

        `started_at` exists for exactly that repair: when the flag says she has
        been here since eight, the row it back-fills must say eight and not the
        moment somebody noticed.
        """
        if not employee:
            return self.browse()
        existing = self.sudo().search([
            ('employee_id', '=', employee.id), ('ended_at', '=', False)], limit=1)
        if existing:
            return existing
        # Savepoint, because the index above turns the race into an error rather
        # than a duplicate, and an error would take the whole page down with it.
        # The loser re-reads the row the winner just made, which is what it was
        # asking for.
        try:
            with self.env.cr.savepoint():
                return self.sudo().create({
                    'employee_id': employee.id,
                    'started_at': started_at or fields.Datetime.now(),
                })
        except psycopg2.IntegrityError:
            return self.sudo().search([
                ('employee_id', '=', employee.id),
                ('ended_at', '=', False)], limit=1)

    @api.model
    def modryn_close(self, employee):
        """She went off. Closes whatever is open, and does nothing if none is.

        Every open row, not just the newest: two rows open for one woman is a
        state nothing should be able to reach, and if it ever happens, leaving
        the older one open would show her as permanently on the floor.
        """
        if not employee:
            return self.browse()
        now = fields.Datetime.now()
        rows = self.sudo().search([
            ('employee_id', '=', employee.id), ('ended_at', '=', False)])
        # A press and an un-press inside a minute is somebody opening the floor
        # and changing her mind, not a shift. Deleted rather than stored: kept,
        # it fills the supervisor's card with "01:48 - 01:48" lines nobody can
        # read past, and any later sum of these would be adding up mis-taps.
        brief = rows.filtered(
            lambda r: r.started_at and (now - r.started_at).total_seconds() < MIN_SPELL_SECONDS)
        kept = rows - brief
        brief.unlink()
        kept.write({'ended_at': now})
        return kept
