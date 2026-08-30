import psycopg2

from odoo import api, fields, models


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
