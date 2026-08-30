from datetime import datetime, time

import pytz

from odoo import fields, http
from odoo.exceptions import ValidationError
from odoo.http import request

from . import access

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

    def _may_close(self, record):
        """May the signed-in user end THIS visit?

        A manager, or the woman on the card - primary or helper. Deliberately
        the same test the board draws its buttons from, so a button that appears
        always works and one that does not appear could not have been used.
        """
        if self._is_manager():
            return True
        me = self._my_employee()
        if not me:
            return False
        return record.modryn_employee_id == me or me in record.modryn_helper_ids

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
        has_outcome = 'modryn_outcome' in env['modryn.queue.entry']._fields
        queue = []
        for position, entry in enumerate(entries, start=1):
            queue.append({
                'id': entry.id,
                'position': position,
                'name': entry.name,
                'phone': entry.phone or '',
                'client_type': entry.client_type,
                'staff_note': entry.staff_note or '',
                'state': entry.state,
                'employee_id': entry.modryn_employee_id.id or False,
                'employee_name': entry.modryn_employee_id.name or '',
                'helpers': [{'id': h.id, 'name': h.name} for h in entry.modryn_helper_ids],
                'room_id': entry.modryn_room_id.id or False,
                # Present only where modryn_ops is installed, which is what
                # carries the outcome fields. Parenthesised: `a or '' if f else
                # ''` binds as `a or ('' if f else '')`, so the guard read as
                # true whatever the flag said.
                'outcome': (entry.modryn_outcome or '') if has_outcome else '',
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
        now = fields.Datetime.now()
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
                # Whether this appointment is HAPPENING, as opposed to being
                # somewhere else on today's list. Decided here because the
                # client is given a wall clock and not a moment, and comparing
                # two wall clocks across a timezone is how "with her now" came
                # to include a bride still forty minutes from the door.
                'in_progress': bool(
                    event.start and event.stop
                    and event.start <= now < event.stop),
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
            # A SEPARATE flag, not a loosened can_assign. That one boolean gates
            # nine different affordances on this board - Done, Invite to book,
            # the booking-outcome select, the ops reopen - whose routes still
            # refuse a plain staff member. Reusing it to show a Take button
            # would hand her four buttons that error when pressed.
            'can_take': self._is_staff(),
        }

    # ------------------------------------------------------------------ page
    @http.route('/floor', type='http', auth='user', website=True, sitemap=False)
    def floor(self, **kw):
        # The PAGE asks the matrix; every ACTION below keeps its level gate.
        if not access.can_view('floor'):
            return access.deny()
        me = self._my_employee()
        # The door. Until she has started her shift the board is not drawn at
        # all - not merely hidden - so a screen left open on an empty counter
        # shows an entry card rather than the room's live state, and "who is on
        # the floor" has an answer somebody typed rather than one inferred from
        # an open tab.
        if me and not me.modryn_on_shift_since:
            return request.render('modryn_staff.floor_start', {
                'me': me,
                'is_manager': self._is_manager(),
            })
        if me:
            # She is on the floor, so a row saying when she came on must exist.
            # It might not: anybody already mid-shift when attendance shipped
            # got the flag without a row, and the door that would create one is
            # not drawn while the flag is set - so the state could not repair
            # itself from any screen. Idempotent, her own row only, and dated
            # from the flag rather than from this page load, because that is
            # when she actually came on.
            request.env['modryn.shift.attendance'].sudo().modryn_open(
                me, me.modryn_on_shift_since)
        return request.render('modryn_staff.floor_page', {
            'board': self._board(),
            'is_manager': self._is_manager(),
            'on_shift_since': me.modryn_on_shift_since if me else None,
        })

    @http.route('/floor/shift/start', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def floor_shift_start(self, **post):
        if not access.can_view('floor'):
            return access.deny()
        me = self._my_employee()
        if me:
            since = me.modryn_on_shift_since
            if not since:
                # sudo(): a portal staff member has no write access to her own
                # hr.employee row, the same reason every other write on this
                # controller goes through it. The group check above is the gate.
                since = fields.Datetime.now()
                me.sudo().modryn_on_shift_since = since
            # OUTSIDE the guard, and that is the fix. The field answers "is she
            # here"; the supervisor's screen asks when she came and when she
            # went, and the field is cleared at exactly the moment the second
            # half of that becomes interesting - so the pair of times lives in
            # its own row.
            #
            # Guarded on the flag, this line never ran for anybody already on
            # the floor when the feature shipped, and no later press could fix
            # it: the guard saw the flag and skipped, so the screen said "on the
            # floor" and "has not come on today" about the same woman for good.
            # modryn_open is idempotent, so calling it every time is free, and
            # a back-filled row is dated from the flag rather than from now.
            request.env['modryn.shift.attendance'].sudo().modryn_open(me, since)
        return request.redirect('/floor')

    @http.route('/floor/shift/end', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def floor_shift_end(self, **post):
        if not access.can_view('floor'):
            return access.deny()
        me = self._my_employee()
        if me:
            me.sudo().modryn_on_shift_since = False
            request.env['modryn.shift.attendance'].sudo().modryn_close(me)
        return request.redirect('/floor')

    # ------------------------------------------------------------------ data
    @http.route('/floor/data', type='jsonrpc', auth='user')
    def floor_data(self):
        # 'forbidden', the code the board's errorText already translates — a
        # new code would render verbatim in the error banner.
        if not access.can_view('floor'):
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

    # ------------------------------------------------- taking a customer
    #
    # These three are STAFF-level, unlike assign/unassign above. A saleswoman
    # picking up the customer standing in front of her is the most ordinary act
    # on the floor, and routing it through a manager was the thing that made the
    # board a manager's tool rather than the team's.
    #
    # The limit is WHO she may move: herself. take() never reads an employee id
    # from the request - it resolves _my_employee() - so a staff member cannot
    # dispatch a colleague onto a customer. That is the same shape the atelier
    # already uses to stop a seamstress handing her work to somebody else.

    @http.route('/floor/take', type='jsonrpc', auth='user')
    def take(self, target, target_id):
        """I have this one."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        record = self._resolve_target(target, target_id)
        me = self._my_employee()
        if not record or not me:
            return {'error': 'not_found'}

        primary = record.modryn_employee_id
        if primary and primary != me:
            # Somebody already has her. Join as a helper rather than shoulder
            # the colleague aside - two people on one bride is normal here, and
            # a silent swap would take a customer off somebody mid-fitting.
            if me not in record.modryn_helper_ids:
                record.write({'modryn_helper_ids': [(4, me.id)]})
        elif not primary:
            record.write({'modryn_employee_id': me.id,
                          'modryn_helper_ids': [(3, me.id)]})
        # Taking a WAITING walk-in is calling her over, and it goes through
        # modryn_call so she gets the text naming her stylist rather than a
        # silent state flip. modryn_call is idempotent on the SMS.
        if target == 'queue' and record.state == 'waiting':
            record.modryn_call(employee=record.modryn_employee_id)
        return self._board()

    @http.route('/floor/release', type='jsonrpc', auth='user')
    def release(self, target, target_id):
        """Back in the line, in the place she checked in at."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        record = self._resolve_target(target, target_id)
        if not record:
            return {'error': 'not_found'}
        if target != 'queue':
            # Only a walk-in has a line to go back to. A booking released this
            # way would lose its staffing and gain nothing.
            return {'error': 'not_found'}
        me = self._my_employee()
        # A manager may release anybody; a staff member only a card she is on.
        # Without this, one saleswoman could put another's customer back in the
        # line from across the shop.
        if not self._is_manager():
            if not me or (record.modryn_employee_id != me
                          and me not in record.modryn_helper_ids):
                return {'error': 'forbidden'}
        record.modryn_release()
        return self._board()

    @http.route('/floor/note', type='jsonrpc', auth='user')
    def set_note(self, target_id, note=None):
        """What the floor needs to remember about her."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        entry = self._resolve_target('queue', target_id)
        if not entry:
            return {'error': 'not_found'}
        entry.write({'staff_note': (note or '').strip()})
        return self._board()

    @http.route('/floor/client-type', type='jsonrpc', auth='user')
    def set_client_type(self, target_id, client_type=None):
        """Bride, or not. Checked against the field's own selection."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        entry = self._resolve_target('queue', target_id)
        if not entry:
            return {'error': 'not_found'}
        valid = dict(entry._fields['client_type'].get_description(
            request.env)['selection'])
        if client_type not in valid:
            return {'error': 'not_found'}
        entry.write({'client_type': client_type})
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
        if not self._is_staff():
            return {'error': 'forbidden'}
        entry = request.env['modryn.queue.entry'].sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        # A manager closes anybody; a saleswoman closes the customer she is
        # actually holding. The old rule was manager-only, which meant the one
        # person who KNOWS the visit ended had to go and find somebody to say so
        # - and a booking has been closable by its own stylist since outcomes
        # existed, so the walk-in was simply the half that never caught up.
        if not self._may_close(entry):
            return {'error': 'forbidden'}
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
            # The modal writes an outcome back against this one, so it has to
            # know which visit it is closing - the customer's name is not an id.
            'entry_id': entry.id,
            'customer': entry.name,
            'phone': entry.phone or '',
            # name, serial and kind travel with each row so the picker can
            # match a prefix in the browser: a thousand dresses is a wall in a
            # <select>, and a round trip per keystroke is worse.
            'variants': [{
                'id': v.id,
                'label': '%s · %s' % (
                    v.product_tmpl_id.name,
                    v.product_template_attribute_value_ids[:1].name or v.name,
                ),
                'name': v.product_tmpl_id.name or '',
                'serial': v.product_tmpl_id.modryn_serial or '',
                'kind': v.product_tmpl_id.modryn_type_id.name or '',
                'size': v.product_template_attribute_value_ids[:1].name or '',
                # Shown beside each one: "which did she take" and "is there
                # another" are the same question at the moment of writing it
                # down, and a size at zero is worth seeing before it is chosen.
                'stock': v.modryn_stock,
            } for v in variants],
        }
        return board

    @http.route('/floor/walkin/outcome', type='jsonrpc', auth='user')
    def walkin_outcome(self, entry_id, outcome, variant_id=None):
        """How the visit ended, and what she carried out of the shop.

        The booking half of this has existed for a while; the walk-in half did
        not, and a walk-in is most brides. So a gown sold across the counter was
        still on the count the next morning, and the catalogue's "how many" was
        quietly wrong in the one direction that costs money.

        Writing modryn_outcome is what moves the count - modryn_ops watches the
        write rather than trusting every caller to remember, which is why there
        is no decrement here to read. Setting it twice to the same dress moves
        nothing, so a double tap on a slow tablet is safe.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        Entry = request.env['modryn.queue.entry']
        if 'modryn_outcome' not in Entry._fields:
            # modryn_ops is what carries the outcome. Without it there is
            # nothing to record and nothing to count.
            return {'error': 'not_found'}
        entry = Entry.sudo().browse(int(entry_id)).exists()
        if not entry:
            return {'error': 'not_found'}
        if not self._may_close(entry):
            return {'error': 'forbidden'}
        if outcome not in ('sold', 'not_sold'):
            return {'error': 'not_found'}

        values = {'modryn_outcome': outcome}
        if outcome == 'sold':
            variant = request.env['product.product'].sudo().browse(
                int(variant_id or 0)).exists()
            if not variant:
                # "Sold" with no dress named would record a sale nobody can
                # count. The stylist is told, rather than left with a tick that
                # did half of what it looked like it did.
                return {'error': 'missing_dress'}
            values['modryn_variant_id'] = variant.id
        else:
            # She bought nothing, so no dress is attached - and clearing it
            # matters, because a correction from sold to not-sold has to give
            # the earlier one back.
            values['modryn_variant_id'] = False
        # She is finished either way: the two buttons this serves are both an
        # ending, and leaving her in the line after one of them would put her
        # back on the board the stylist just cleared.
        if entry.state in ('waiting', 'called'):
            entry.action_done()
        entry.write(values)
        return self._board()

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
