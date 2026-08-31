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

# Sixteen hours. Only ever applied to a spell somebody TYPED - the floor's own
# open/close is not measured against it, because a woman who genuinely forgot to
# press the button overnight still needs the row that says so before it can be
# corrected. This is the guard on the correction itself: a manager fixing
# Tuesday who fills in the wrong date turns one shift into a fortnight, and that
# figure would then be in her month, her totals and her pay.
MAX_SPELL_SECONDS = 16 * 3600


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
                # The id, so the manager's copy of this month can name the row
                # she is correcting. Her own profile ignores it.
                'id': row.id,
                'in': local.strftime('%H:%M'),
                'out': (self._modryn_local(row.ended_at).strftime('%H:%M')
                        if row.ended_at else ''),
                'open': not row.ended_at,
                'date': local.strftime('%Y-%m-%d'),
                'hours': round(seconds / 3600.0, 2),
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
            # Days and spells are different numbers and the difference matters:
            # a woman who goes home for lunch and comes back worked ONE day and
            # left two rows. "How many shifts in March" is the day count; the
            # spell count is only shown where they disagree, so the page never
            # argues with itself.
            'spell_count': sum(len(d['spells']) for d in days),
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

    # ------------------------------------------------- correcting the record
    @api.model
    def _modryn_utc(self, day, clock):
        """A date and an "HH:MM" the boutique typed, as the naive UTC we store.

        Localised through the SAME TZ every reader here uses. A correction typed
        as 18:30 and written straight into the column would be 18:30 UTC, which
        is half past nine on the boutique's own clock in summer - the shift would
        move three hours by being corrected.
        """
        parts = (clock or '').split(':')
        if len(parts) != 2:
            return None
        try:
            hour, minute = int(parts[0]), int(parts[1])
            stamp = datetime.combine(day, time(hour=hour, minute=minute))
        except (TypeError, ValueError):
            return None
        return TZ.localize(stamp).astimezone(pytz.utc).replace(tzinfo=None)

    def modryn_amend(self, day, came, went):
        """Set one spell's hours. Returns an error key, or None on success.

        `went` empty means she is still on the floor - which is a real answer,
        not a missing one: the manager may be fixing this morning's start time
        while the woman is standing in the room.

        Every refusal here is a figure that would go on to be WRONG rather than
        merely odd. An end before its start is the two times entered the wrong
        way round; an overlap double-counts the same minutes into her month; a
        second open row breaks what the database is already promising with an
        index.
        """
        self.ensure_one()
        started = self._modryn_utc(day, came)
        if not started:
            return 'badtime'
        ended = None
        if went:
            ended = self._modryn_utc(day, went)
            if not ended:
                return 'badtime'
            # REFUSED, not rolled to the next day. This used to add a day and
            # call it a shift running past midnight, which made the commonest
            # typo on this form - the two times the wrong way round - succeed,
            # and succeed as a row nobody would question later. Measured:
            # 20:00 -> 10:00 became a fourteen-hour overnight shift and passed
            # every guard here, being under the ceiling and overlapping nothing.
            #
            # A shift that really does run past midnight is two rows, one to
            # 23:59 and one from 00:00 the next day - which is also the honest
            # shape, because those hours fell on two different days and every
            # total on this screen is grouped by day.
            if ended <= started:
                return 'backwards'
            if (ended - started).total_seconds() > MAX_SPELL_SECONDS:
                return 'toolong'
        if not ended and self.sudo().search_count([
                ('employee_id', '=', self.employee_id.id),
                ('ended_at', '=', False), ('id', '!=', self.id)]):
            return 'alreadyopen'
        # Overlap, against every OTHER row of hers. An open row counts to now,
        # because a spell that ends inside one still open is the same minutes
        # twice however the open one eventually closes.
        horizon = ended or fields.Datetime.now()
        clash = self.sudo().search([
            ('employee_id', '=', self.employee_id.id),
            ('id', '!=', self.id),
            ('started_at', '<', horizon),
            '|', ('ended_at', '=', False), ('ended_at', '>', started),
        ], limit=1)
        if clash:
            # Told apart, because the two need different things done. A closed
            # shift crossing this one is a typo in one of the two times. An OPEN
            # one from days earlier is a woman who never pressed the button
            # going home: it covers every hour since, so it collides with any
            # correction anywhere after it, and "that crosses another shift the
            # same day" sends the manager looking at the wrong day entirely.
            return 'openbefore' if not clash.ended_at else 'overlap'
        self.sudo().write({'started_at': started, 'ended_at': ended})
        return None

    @api.model
    def modryn_add_spell(self, employee, day, came, went):
        """A shift nobody pressed the button for. Same rules as amending."""
        if not employee:
            return 'nobody'
        row = self.sudo().create({
            'employee_id': employee.id,
            # A placeholder the amend below immediately replaces. Created first
            # so the overlap check has a real id to exclude - checking before
            # the row exists means writing the same guard twice.
            'started_at': fields.Datetime.now(),
            'ended_at': fields.Datetime.now(),
        })
        error = row.modryn_amend(day, came, went)
        if error:
            row.unlink()
        return error

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
