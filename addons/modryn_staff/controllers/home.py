from datetime import datetime, time

import pytz

from odoo import http
from odoo.http import request

from . import access

TZ = pytz.timezone('Asia/Jerusalem')


class ModrynHome(http.Controller):
    """A staff member's own day: her customers, her work, her shift.

    The floor board shows the whole room and belongs, by default, to managers;
    this page shows only what is HERS. Downstream modules add their sections
    by controller inheritance — the exact pattern ModrynFloor._board() set —
    and the template renders a section only when its key is present, the
    floor_board.xml precedent for ops-injected keys.
    """

    def _my_employee(self):
        user = request.env.user
        if not user or user._is_public():
            return None
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1) or None

    def _home_today_bounds_utc(self):
        today = datetime.now(TZ).date()
        start = TZ.localize(datetime.combine(today, time.min)).astimezone(
            pytz.utc).replace(tzinfo=None)
        end = TZ.localize(datetime.combine(today, time.max)).astimezone(
            pytz.utc).replace(tzinfo=None)
        return start, end

    def _home(self):
        me = self._my_employee()
        home = {
            'name': request.env.user.name,
            'role': (me.modryn_role_id.name or '') if me and me.modryn_role_id else '',
            # Two different banners: an account with no employee record is an
            # owner-side wiring problem; an employee with no role just needs
            # the owner to pick one.
            'no_employee': me is None,
            'no_role': bool(me) and not me.modryn_role_id,
            'walkins': [],
            'bookings': [],
        }
        if not me:
            return home

        for entry in request.env['modryn.queue.entry'].sudo().search([
                ('modryn_employee_id', '=', me.id),
                ('state', 'in', ('waiting', 'called'))]):
            home['walkins'].append({'name': entry.name, 'state': entry.state})

        start, end = self._home_today_bounds_utc()
        Event = request.env['calendar.event'].sudo()
        helper_event_ids = request.env['modryn.floor.helper'].sudo().search([
            ('employee_id', '=', me.id), ('event_id', '!=', False),
        ]).event_id.ids
        domain = [
            ('modryn_is_booking', '=', True),
            ('start', '>=', start), ('start', '<=', end),
            '|', ('modryn_employee_id', '=', me.id),
            ('id', 'in', helper_event_ids),
        ]
        # The field ships with modryn_portal, which may not be installed.
        if 'modryn_cancelled_at' in Event._fields:
            domain = ['&', ('modryn_cancelled_at', '=', False)] + domain
        for event in Event.search(domain, order='start asc'):
            home['bookings'].append({
                'time': pytz.utc.localize(event.start).astimezone(TZ).strftime('%H:%M'),
                'title': event.name,
                'helper': event.modryn_employee_id.id != me.id,
            })
        return home

    @http.route('/staff/home', type='http', auth='user', website=True, sitemap=False)
    def staff_home(self, **kw):
        # can_view('home') is just "is boutique staff" — the one page no
        # matrix state can take away.
        if not access.can_view('home'):
            return access.deny()
        return request.render('modryn_staff.staff_home', {
            'home': self._home(),
            'active_tab': 'home',
        })
