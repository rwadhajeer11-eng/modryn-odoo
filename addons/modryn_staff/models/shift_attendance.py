from odoo import api, fields, models


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
    _description = 'A stretch of time a woman was on the floor'
    _order = 'started_at desc, id desc'

    employee_id = fields.Many2one(
        'hr.employee', required=True, index=True, ondelete='cascade')
    started_at = fields.Datetime(required=True, index=True)
    # Empty means she is still on the floor. Not defaulted to the start: a
    # zero-length shift and an open one would then look the same, and only one
    # of them is somebody standing in the room.
    ended_at = fields.Datetime()

    @api.model
    def modryn_open(self, employee):
        """She came on. Returns the row, or the one already open.

        Idempotent on purpose: the floor page can be opened twice, and a second
        row would put the same woman on the floor twice over on the supervisor's
        screen.
        """
        if not employee:
            return self.browse()
        existing = self.sudo().search([
            ('employee_id', '=', employee.id), ('ended_at', '=', False)], limit=1)
        if existing:
            return existing
        return self.sudo().create({
            'employee_id': employee.id,
            'started_at': fields.Datetime.now(),
        })

    @api.model
    def modryn_close(self, employee):
        """She went off. Closes whatever is open, and does nothing if none is.

        Every open row, not just the newest: two rows open for one woman is a
        state nothing should be able to reach, and if it ever happens, leaving
        the older one open would show her as permanently on the floor.
        """
        if not employee:
            return self.browse()
        rows = self.sudo().search([
            ('employee_id', '=', employee.id), ('ended_at', '=', False)])
        rows.write({'ended_at': fields.Datetime.now()})
        return rows
