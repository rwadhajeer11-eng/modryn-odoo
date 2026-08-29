from datetime import timedelta

from odoo import _, http
from odoo.http import request

from odoo.addons.modryn_staff.controllers.floor import ModrynFloor
from odoo.addons.modryn_staff.controllers import access
from odoo.addons.modryn_staff.controllers.home import ModrynHome

from ..models.shift_slot import today, week_start
from ..models.shift_template import (
    SHIFT_TYPE_ORDER, shift_type_selection, weekday_name)


def _clock(hour_float):
    return '%02d:%02d' % (int(hour_float), round((hour_float % 1) * 60))


class ModrynHomeRoster(ModrynHome):
    """Today's shift, on her own page: is she on the published rota, and when."""

    def _home(self):
        """Two views of the same week, because they answer different questions.

        MY SHIFTS is "when am I in?" - only the shifts she is actually on, so
        she can read her own week off the top of the page without hunting.

        THE WEEK is "who is in, and when?" - every published shift, including
        the ones she is NOT working. That is the point of it: knowing the shop
        is covered on Friday, or who to ask to swap, means seeing the shifts
        that are not hers.

        PUBLISHED only, both of them. An unpublished week is a manager's draft
        and half of it is usually wrong; showing it would have the team planning
        their lives around a rota nobody has agreed to.
        """
        home = super()._home()
        me = self._my_employee()
        home['shift'] = []
        home['my_shifts'] = []
        home['week_shifts'] = []
        # Whether a rota exists for this week AT ALL, which is a different
        # answer from "a published day with nobody on it" - see below.
        home['week_published'] = False
        if not me:
            return home

        Slot = request.env['modryn.shift.slot'].sudo()
        type_labels = dict(shift_type_selection())
        start = week_start()
        slots = Slot.search([('week_start', '=', start), ('published', '=', True)],
                            order='day asc, start_hour asc')

        # Kept for the "today" strip that was here before; it is the same
        # question narrowed to one day, so it reads off the same query rather
        # than running a second one.
        home['shift'] = [{
            'name': slot.modryn_name(),
            'hours': '%s-%s' % (_clock(slot.start_hour), _clock(slot.end_hour)),
        } for slot in slots if slot.day == today() and me in slot.employee_ids]

        by_day = {}
        for slot in slots:
            row = {
                'name': slot.modryn_name(),
                'hours': '%s-%s' % (_clock(slot.start_hour), _clock(slot.end_hour)),
                'type': slot._shift_type(),
                'type_label': type_labels.get(slot._shift_type(), ''),
                # Names, not ids: this is a list somebody reads, and the board
                # is the place to go if she wants to change it.
                'people': [e.name for e in slot.employee_ids if e.active],
                'mine': me in slot.employee_ids,
            }
            by_day.setdefault(slot.day, []).append(row)
            if row['mine']:
                home['my_shifts'].append(dict(
                    row, day_label=slot.day.strftime('%d.%m'),
                    weekday=weekday_name(slot.day)))

        # THE ROTA IS NOT PUBLIC TO EVERY ROLE. my_shifts and today are hers by
        # name and always shown; the cross-staff panel answers "who else is in?"
        # and belongs to whoever the owner has granted the work schedule to.
        # Without this a saleswoman whose role has Work schedule unticked - or
        # who has no role yet - gets the themed 403 on /roster and the complete
        # team rota on her own front page, which makes the matrix a suggestion.
        # All three seeded roles carry the grant, so nobody loses it today.
        if not access.can_view('roster'):
            return home

        home['week_published'] = bool(slots)

        # Every day of the week, in order, INCLUDING the days with nothing on
        # them. A missing Friday and a closed Friday look identical if the day
        # simply is not drawn, and only one of them is worth asking about.
        for offset in range(7):
            day = start + timedelta(days=offset)
            home['week_shifts'].append({
                'day_label': day.strftime('%d.%m'),
                'weekday': weekday_name(day),
                'is_today': day == today(),
                'slots': sorted(by_day.get(day, []),
                                key=lambda r: SHIFT_TYPE_ORDER.index(r['type'])
                                if r['type'] in SHIFT_TYPE_ORDER else 99),
            })
        return home


class ModrynFloorRoster(ModrynFloor):
    """Controller inheritance, the same seam the atelier and ops already use:
    the floor board learns about the published rota only when modryn_roster is
    installed. modryn_staff itself stays roster-ignorant — and the dependency
    only points this way, so it keeps working without this module.

    This is what makes publishing a week mean something outside /roster.
    """

    def _rostered_today(self):
        return request.env['modryn.shift.slot'].sudo().modryn_rostered_on(today())

    def _board(self):
        board = super()._board()
        rostered = self._rostered_today()
        for row in board['staff']:
            # FLAG, never filter. This one list feeds five surfaces — the bench,
            # both assignee <select> fallbacks, the free-staff count and the
            # alteration picker — and the off-roster colleague covering for a
            # sick friend has to stay assignable. That is the whole reason the
            # rota warns here rather than blocking.
            row['rostered'] = True if rostered is None else row['id'] in rostered
            # The LABEL is built here, not in the OWL template, and that is a
            # translation decision rather than a style one. Terms inside
            # static/src/**.xml only reach the Hebrew catalogue through a POT
            # re-export carrying an `odoo-javascript` comment; a hand-written
            # msgid silently fails to apply. This module's Python terms already
            # translate, and the staff terminal defaults to he_IL — so the
            # server sends the sentence and the template just prints it.
            row['off_rota_label'] = '' if row['rostered'] else _("Off rota today")
        return board

    # A bare @route() inherits the parent's url, type and auth rather than
    # restating them (an undecorated override still works, but Odoo logs a
    # warning about it on every boot).
    @http.route()
    def assign(self, target, target_id, employee_id, as_primary=False):
        board = super().assign(target, target_id, employee_id, as_primary=as_primary)
        if board.get('error'):
            return board
        # The flag super()._board() just computed already knows about the
        # no-published-rota fallback, so there is nothing to re-derive here.
        row = next((s for s in board['staff'] if s['id'] == int(employee_id)), None)
        if row and not row['rostered']:
            board['warning'] = _("%s isn't on today's rota.", row['name'])
        return board
