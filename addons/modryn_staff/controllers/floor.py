from datetime import datetime, time

import pytz

from odoo import http
from odoo.http import request

TZ = pytz.timezone('Asia/Jerusalem')

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'


class ModrynFloor(http.Controller):
    """The in-store terminal: who is waiting, what is booked, who is free.

    Managers assign; plain staff read. That distinction is enforced in every
    action route below, not merely by hiding buttons — a staff member who calls
    /floor/assign/queue directly must be refused.

    hr.employee is ACL-restricted to hr.group_hr_user and carries private HR
    fields, so nothing here hands a recordset to the client: every route returns
    plain dicts built under sudo().
    """

    # ---------------------------------------------------------------- helpers
    def _is_staff(self):
        user = request.env.user
        return bool(user) and not user._is_public() and user.has_group(GROUP_STAFF)

    def _is_manager(self):
        user = request.env.user
        return bool(user) and not user._is_public() and user.has_group(GROUP_MANAGER)

    def _today_bounds_utc(self):
        """Today in Israel, expressed in the UTC the database stores."""
        today = datetime.now(TZ).date()
        start = TZ.localize(datetime.combine(today, time.min)).astimezone(pytz.utc)
        end = TZ.localize(datetime.combine(today, time.max)).astimezone(pytz.utc)
        return start.replace(tzinfo=None), end.replace(tzinfo=None)

    def _board(self):
        env = request.env

        entries = env['modryn.queue.entry'].sudo().search([('state', '!=', 'done')])
        queue = []
        for position, entry in enumerate(entries, start=1):
            queue.append({
                'id': entry.id,
                'position': position,
                'name': entry.name,
                'phone': entry.phone or '',
                'client_type': entry.client_type,
                'state': entry.state,
                'employee_id': entry.modryn_employee_id.id or False,
                'employee_name': entry.modryn_employee_id.name or '',
            })

        day_start, day_end = self._today_bounds_utc()
        events = env['calendar.event'].sudo().search(
            [('modryn_is_booking', '=', True),
             ('start', '>=', day_start),
             ('start', '<=', day_end)],
            order='start asc')
        bookings = []
        for event in events:
            variant = event.modryn_variant_id
            bookings.append({
                'id': event.id,
                # Stored UTC, shown in Israeli local time — the staff read a wall clock.
                'time': pytz.utc.localize(event.start).astimezone(TZ).strftime('%H:%M'),
                'title': event.name,
                'phone': event.modryn_customer_phone or '',
                'dress': variant.product_tmpl_id.name if variant else '',
                'size': variant.product_template_attribute_value_ids[:1].name if variant else '',
                'employee_id': event.modryn_employee_id.id or False,
                'employee_name': event.modryn_employee_id.name or '',
            })

        employees = env['hr.employee'].sudo().search([
            ('modryn_level', 'in', ['manager', 'staff']),
        ])
        staff = [{
            'id': e.id,
            'name': e.name,
            'role': e.modryn_role_id.name or '',
            'occupied': e.modryn_is_occupied,
            'occupied_with': e.modryn_occupied_with or '',
        } for e in employees]

        return {
            'queue': queue,
            'bookings': bookings,
            'staff': staff,
            'can_assign': self._is_manager(),
        }

    # ------------------------------------------------------------------ page
    @http.route('/floor', type='http', auth='user', website=True, sitemap=False)
    def floor(self, **kw):
        if not self._is_staff():
            return request.not_found()
        return request.render('modryn_staff.floor_page', {
            'board': self._board(),
            'is_manager': self._is_manager(),
        })

    # ------------------------------------------------------------------ data
    @http.route('/floor/data', type='jsonrpc', auth='user')
    def floor_data(self):
        if not self._is_staff():
            return {'error': 'forbidden'}
        return self._board()

    # --------------------------------------------------------------- actions
    @http.route('/floor/assign/queue', type='jsonrpc', auth='user')
    def assign_queue(self, entry_id, employee_id):
        if not self._is_manager():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        if not entry or not employee:
            return {'error': 'not_found'}
        entry.modryn_assign(employee)
        return self._board()

    @http.route('/floor/assign/booking', type='jsonrpc', auth='user')
    def assign_booking(self, event_id, employee_id):
        if not self._is_manager():
            return {'error': 'forbidden'}
        event = request.env['calendar.event'].sudo().browse(int(event_id)).exists()
        employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        if not event or not event.modryn_is_booking or not employee:
            return {'error': 'not_found'}
        event.modryn_employee_id = employee
        return self._board()

    @http.route('/floor/queue/done', type='jsonrpc', auth='user')
    def queue_done(self, entry_id):
        if not self._is_manager():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        # Freeing the entry also frees whoever was serving it: occupancy is
        # derived, so nobody has to remember to flip a status back.
        entry.write({'state': 'done'})
        return self._board()
