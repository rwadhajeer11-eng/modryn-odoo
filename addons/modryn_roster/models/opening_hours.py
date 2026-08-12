from odoo import api, models

# Who counts as "on the floor". The same two levels the floor board's own team
# list uses (modryn_staff/controllers/floor.py), so the board and the booking
# grid cannot disagree about who is working: an owner doing the books is on the
# rota and is not a stylist, and a day staffed by her alone sells nothing.
FLOOR_LEVELS = ['manager', 'staff']


class ModrynOpeningHours(models.Model):
    """The published rota caps the booking grid.

    Model inheritance, the same seam floor_roster.py uses for the controller:
    /book learns that a rota exists only when this module is installed, and
    modryn_booking stays rota-ignorant — which it must, since the dependency
    runs modryn_roster -> modryn_staff -> modryn_booking and never back.
    """

    _inherit = 'modryn.opening.hours'

    @api.model
    def modryn_daily_caps(self, days):
        caps = super().modryn_daily_caps(days)
        if not days:
            return caps

        Slot = self.env['modryn.shift.slot'].sudo()
        # ONE search for the whole fortnight, not modryn_rostered_on() per day:
        # /book's cost is a fixed number of queries by design, and fourteen calls
        # is exactly the shape F2 and F3 spent effort removing.
        #
        # That batching is the ONLY thing that differs from
        # modryn.shift.slot.modryn_rostered_on(). The rule itself is the same and
        # must stay the same: same `published` filter, same union across a day's
        # slots, and the `if working:` guard below is that method's
        # `rostered or None` written in another shape — no key here is its None.
        # The guard here is deliberately WIDER: it also stays silent for a day
        # rostered only by people who do not work the floor, which
        # modryn_rostered_on has no reason to care about. If either rule changes,
        # the other has to move with it, and the floor board is the other reader.
        by_day = {}
        for slot in Slot.search([('day', 'in', list(days)), ('published', '=', True)]):
            by_day.setdefault(slot.day, set()).update(slot.employee_ids.ids)

        everyone = set().union(*by_day.values()) if by_day else set()
        if not everyone:
            return caps
        # One more search, not one per day, for the same reason. Archived staff
        # drop out here via active_test: someone who left in March is still
        # attached to a published March shift and is nobody the boutique can sell.
        on_floor = set(self.env['hr.employee'].sudo().search([
            ('id', 'in', list(everyone)),
            ('modryn_level', 'in', FLOOR_LEVELS),
        ]).ids)

        for day, rostered in by_day.items():
            working = len(rostered & on_floor)
            # NO KEY when nobody qualifies, never a cap of 0. Two ways to get
            # here and both are "the rota has nothing to say", not "the boutique
            # is shut": a published week whose slots name nobody (publishing is
            # week-wide, so a manager who fills Sunday leaves Monday to Thursday
            # published and empty), and a day rostered entirely by people who do
            # not work the floor. Emitting 0 for either would empty every hour of
            # that day on a page nobody would think to check.
            if working:
                caps[day] = working
        return caps
