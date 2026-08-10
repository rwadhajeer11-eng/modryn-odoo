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
                'helpers': [{'id': h.id, 'name': h.name} for h in entry.modryn_helper_ids],
            })

        day_start, day_end = self._today_bounds_utc()
        booking_domain = [('modryn_is_booking', '=', True),
                          ('start', '>=', day_start),
                          ('start', '<=', day_end)]
        # A cancelled appointment is not on today's floor. The field ships with
        # modryn_portal, which may not be installed.
        if 'modryn_cancelled_at' in env['calendar.event']._fields:
            booking_domain.append(('modryn_cancelled_at', '=', False))
        events = env['calendar.event'].sudo().search(booking_domain, order='start asc')
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
                'helpers': [{'id': h.id, 'name': h.name} for h in event.modryn_helper_ids],
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
    def _resolve_target(self, target, target_id):
        """The card being acted on: a walk-in or a booking, or None."""
        if target == 'queue':
            record = request.env['modryn.queue.entry'].sudo().browse(int(target_id)).exists()
            return record or None
        if target == 'booking':
            record = request.env['calendar.event'].sudo().browse(int(target_id)).exists()
            return record if (record and record.modryn_is_booking) else None
        return None

    @http.route('/floor/assign', type='jsonrpc', auth='user')
    def assign(self, target, target_id, employee_id, as_primary=False):
        """Put an employee on a customer card.

        The locked rule, encoded once: the first person on a card becomes its
        primary; anyone after joins as a helper; dropping onto the primary SLOT
        (as_primary) swaps, demoting the old primary to helper rather than
        losing her — she is still physically with the customer.
        """
        if not self._is_manager():
            return {'error': 'forbidden'}
        record = self._resolve_target(target, target_id)
        employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        if not record or not employee:
            return {'error': 'not_found'}

        current_primary = record.modryn_employee_id
        if employee == current_primary:
            return self._board()  # already exactly where she was dropped

        values = {}
        if as_primary or not current_primary:
            values['modryn_employee_id'] = employee.id
            # Never lose the previous primary; never keep the new primary
            # duplicated in the helper list.
            helper_ops = [(3, employee.id)]
            if as_primary and current_primary:
                helper_ops.append((4, current_primary.id))
            values['modryn_helper_ids'] = helper_ops
        elif employee in record.modryn_helper_ids:
            return self._board()  # no-op: already a helper on this card
        else:
            values['modryn_helper_ids'] = [(4, employee.id)]

        # Assigning someone to a waiting walk-in IS calling her over.
        if target == 'queue' and record.state == 'waiting':
            values['state'] = 'called'
        record.write(values)
        return self._board()

    @http.route('/floor/unassign', type='jsonrpc', auth='user')
    def unassign(self, target, target_id, employee_id):
        if not self._is_manager():
            return {'error': 'forbidden'}
        record = self._resolve_target(target, target_id)
        employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        if not record or not employee:
            return {'error': 'not_found'}

        if record.modryn_employee_id == employee:
            # The card must not go headless while people are still on it: a
            # helper steps up. ponytail: an m2m reads in the comodel's _order,
            # so "first" here means first by name, not longest-serving — true
            # join-order promotion needs a through-model with a joined_at.
            helpers = record.modryn_helper_ids
            promoted = helpers[:1]
            record.write({
                'modryn_employee_id': promoted.id if promoted else False,
                'modryn_helper_ids': [(3, promoted.id)] if promoted else [(5,)],
            })
        else:
            record.write({'modryn_helper_ids': [(3, employee.id)]})
        return self._board()

    @http.route('/floor/finish', type='jsonrpc', auth='user')
    def finish(self, entry_id):
        """Close a walk-in and hand the board what the finish modal needs.

        Freeing the entry frees everyone on it (occupancy is derived), and the
        response carries the customer + dress list so the manager can open an
        alteration task without a second round-trip.
        """
        if not self._is_manager():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        entry.write({'state': 'done'})

        variants = request.env['product.product'].sudo().search([
            ('product_tmpl_id.is_published', '=', True),
        ])
        board = self._board()
        board['finished'] = {
            'customer': entry.name,
            'phone': entry.phone or '',
            'variants': [{
                'id': v.id,
                'label': '%s · %s' % (
                    v.product_tmpl_id.name,
                    v.product_template_attribute_value_ids[:1].name or v.name,
                ),
            } for v in variants],
        }
        return board
