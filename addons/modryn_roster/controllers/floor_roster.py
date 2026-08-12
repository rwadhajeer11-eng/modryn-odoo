from odoo import _, http
from odoo.http import request

from odoo.addons.modryn_staff.controllers.floor import ModrynFloor

from ..models.shift_slot import today


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
