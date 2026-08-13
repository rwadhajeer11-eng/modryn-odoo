from datetime import datetime, time

import pytz

from odoo import http
from odoo.exceptions import ValidationError
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

    def _my_employee(self):
        """The employee record behind the signed-in user, or an empty one."""
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _today_bounds_utc(self):
        """Today in Israel, expressed in the UTC the database stores."""
        today = datetime.now(TZ).date()
        start = TZ.localize(datetime.combine(today, time.min)).astimezone(pytz.utc)
        end = TZ.localize(datetime.combine(today, time.max)).astimezone(pytz.utc)
        return start.replace(tzinfo=None), end.replace(tzinfo=None)

    def _board(self):
        env = request.env

        pending_entries = env['modryn.queue.entry'].sudo().search([('state', '=', 'pending')])
        pending = [{
            'id': e.id,
            'name': e.name,
            'phone': e.phone or '',
            'client_type': e.client_type,
        } for e in pending_entries]

        entries = env['modryn.queue.entry'].sudo().search([('state', 'in', ('waiting', 'called'))])
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
                'room_id': entry.modryn_room_id.id or False,
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
                'room_id': event.modryn_room_id.id or False,
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

        rooms = env['modryn.fitting.room'].sudo().search([])
        me = self._my_employee()

        # Only calls this board should react to: mine to answer, or mine to
        # watch because I raised them. A saleswoman must not get an overlay for
        # a call between two colleagues on the other side of the floor.
        sos_domain = [('state', 'in', ('open', 'acked'))]
        calls = env['modryn.sos.call'].sudo().search(sos_domain)
        mine = calls.filtered(lambda c: (
            (me and c.caller_id == me)
            or (me and c.target_id == me)
            or (not c.target_id and self._is_manager())
        ))

        return {
            'pending': pending,
            'queue': queue,
            'bookings': bookings,
            'staff': staff,
            'rooms': [{'id': r.id, 'name': r.name} for r in rooms],
            'me': {'id': me.id, 'name': me.name} if me else None,
            'sos': [c._row() for c in mine],
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

        record.write(values)
        # Assigning someone to a waiting walk-in IS calling her over — through
        # modryn_call, so she gets the "we're ready for you" text naming the
        # stylist rather than a silent state flip.
        if target == 'queue' and record.state == 'waiting':
            record.modryn_call(employee=record.modryn_employee_id)
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
            # The card must not go headless while people are still on it: the
            # LONGEST-SERVING helper steps up. That order comes from the
            # modryn.floor.helper through-model, because a plain m2m reads in
            # employee-name order and would promote whoever is alphabetically
            # first — a fact about the alphabet, not about the floor.
            promoted = record.modryn_oldest_helper()
            record.write({
                'modryn_employee_id': promoted.id if promoted else False,
                'modryn_helper_ids': [(3, promoted.id)] if promoted else [(5,)],
            })
        else:
            record.write({'modryn_helper_ids': [(3, employee.id)]})
        return self._board()

    @http.route('/floor/accept', type='jsonrpc', auth='user')
    def accept(self, entry_id):
        """Let her into the line.

        Idempotent by state: two managers tapping at once produce one
        transition, and one 'you're next' text — not two.
        """
        if not self._is_manager():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        entry.modryn_accept()
        return self._board()

    @http.route('/floor/redirect', type='jsonrpc', auth='user')
    def redirect_to_booking(self, entry_id):
        """Too busy — invite her to book instead. She is never told she was
        turned away; her page simply becomes a warm invitation."""
        if not self._is_manager():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        entry.modryn_redirect()
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
        # action_done(), not a bare write: it makes the same state change AND
        # promotes whoever is now at the front. This route wrote the state
        # directly, so finishing a customer on the floor terminal never sent the
        # next bride her "you're next". It went unnoticed because every
        # acceptance also swept the queue — and acceptance is now gone.
        entry.action_done()

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

    # ------------------------------------------------------------- rooms
    @http.route('/floor/room', type='jsonrpc', auth='user')
    def set_room(self, target, target_id, room_id=None):
        """Put a customer in a fitting room, or take her out of one.

        Staff may do this, not only managers: the woman who walks a customer to
        a room is whoever is with her, and making her find a manager first is
        how the registry would end up permanently wrong.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        record = self._resolve_target(target, target_id)
        if not record:
            return {'error': 'not_found'}
        try:
            # The savepoint is load-bearing, not decoration. Catching the
            # ValidationError stops Odoo's handler from rolling the request
            # back, but it does NOT undo the write the constraint rejected — so
            # without this the board said "Room 1 is taken" and then committed
            # BOTH customers into Room 1 anyway.
            with request.env.cr.savepoint():
                record.write({'modryn_room_id': int(room_id) if room_id else False})
        except ValidationError as exc:
            # Two customers in one room is a real collision on the floor, so it
            # comes back as a message the board can show, not a 500.
            return dict(self._board(), error=exc.args[0] if exc.args else 'invalid')
        return self._board()

    # --------------------------------------------------------------- SOS
    @http.route('/floor/sos', type='jsonrpc', auth='user')
    def sos(self, target='manager', target_id=None, card=None, card_id=None, note=None):
        """Ask for help. Any member of staff may, including a manager."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        me = self._my_employee()
        if not me:
            return {'error': 'no_employee'}

        colleague = None
        if target == 'employee' and target_id:
            colleague = request.env['hr.employee'].sudo().browse(int(target_id)).exists()
            if not colleague:
                return {'error': 'not_found'}

        record = self._resolve_target(card, card_id) if card and card_id else None
        request.env['modryn.sos.call'].sudo().modryn_raise(
            caller=me, target=colleague, record=record, note=note)
        return self._board()

    @http.route('/floor/sos/ack', type='jsonrpc', auth='user')
    def sos_ack(self, call_id):
        if not self._is_staff():
            return {'error': 'forbidden'}
        call = request.env['modryn.sos.call'].sudo().browse(int(call_id)).exists()
        if not call:
            return {'error': 'not_found'}
        call.modryn_ack(self._my_employee())
        return self._board()

    @http.route('/floor/sos/resolve', type='jsonrpc', auth='user')
    def sos_resolve(self, call_id):
        if not self._is_staff():
            return {'error': 'forbidden'}
        call = request.env['modryn.sos.call'].sudo().browse(int(call_id)).exists()
        if not call:
            return {'error': 'not_found'}
        call.modryn_resolve()
        return self._board()
