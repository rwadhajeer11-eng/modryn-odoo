from datetime import datetime, timedelta

import pytz

from odoo import http
from odoo.http import request

from odoo.addons.modryn_staff.controllers.floor import ModrynFloor

from ..models.calendar_event import UNCLOSED_WINDOW_DAYS

TZ = pytz.timezone('Asia/Jerusalem')


class ModrynFloorOps(ModrynFloor):
    """Controller inheritance, same trick as the atelier: the floor board
    learns about outcomes only when modryn_ops is installed. modryn_staff
    itself stays outcome-ignorant and keeps working without this module."""

    def _board(self):
        board = super()._board()
        env = request.env

        # Today's checklist materialises on first read — the lazy pattern the
        # roster established. A closed day leaves no orphan rows behind.
        Task = env['modryn.task'].sudo()
        Task.modryn_ensure_today()
        today = datetime.now(TZ).date()
        board['checklist'] = [t._row() for t in Task.search([
            ('template_id', '!=', False), ('day', '=', today)])]

        # Follow-up work: staff see their own; a manager sees the whole pile,
        # because unassigned follow-ups would otherwise be invisible to the
        # only people who can hand them out.
        me = self._my_employee()
        followup_domain = [('state', '=', 'open'), ('template_id', '=', False)]
        if self._is_manager():
            board['ops_tasks'] = [t._row() for t in Task.search(followup_domain)]
        elif me:
            board['ops_tasks'] = [t._row() for t in Task.search(
                followup_domain + [('employee_id', '=', me.id)])]
        else:
            board['ops_tasks'] = []

        ids = [b['id'] for b in board['bookings']]
        if ids:
            events = {e.id: e for e in env['calendar.event'].sudo().browse(ids)}
            for b in board['bookings']:
                event = events.get(b['id'])
                b['outcome'] = (event.modryn_outcome or '') if event else ''
                # The correction modal prefills from these — a re-save must
                # carry the recorded figures, not silently zero them.
                b['sale_amount'] = event.modryn_sale_amount if event else 0.0
                b['sale_items'] = (event.modryn_sale_items or '') if event else ''

        # The nag that makes conversion numbers trustworthy: a past booking
        # with no outcome is a number quietly leaking out of every report.
        # Managers see the count until it reaches zero. Windowed, so bookings
        # that predate this feature don't bury today's real ones.
        if self._is_manager():
            board['unclosed_count'] = env['calendar.event'].sudo().search_count(
                self._unclosed_domain())
        return board

    def _unclosed_domain(self):
        """Past bookings still without an ending. active_test=False matters:
        archiving a PAST booking does not cancel it (the archive-is-cancel
        override deliberately converts only future ones), so without this an
        archived row would slip out of the nag with its outcome forever
        unrecorded — the exact leak the badge exists to prevent."""
        now = datetime.utcnow()
        return [
            ('modryn_is_booking', '=', True),
            ('modryn_cancelled_at', '=', False),
            ('modryn_outcome', '=', False),
            ('start', '<', now),
            ('start', '>=', now - timedelta(days=UNCLOSED_WINDOW_DAYS)),
            ('active', 'in', (True, False)),
        ]

    @http.route('/floor/unclosed', type='jsonrpc', auth='user')
    def unclosed(self):
        """The rows behind the nag badge — a manager cannot drive the count to
        zero from a board that only renders today, so the badge opens this."""
        if not self._is_manager():
            return {'error': 'forbidden'}
        events = request.env['calendar.event'].sudo().search(
            self._unclosed_domain(), order='start desc')
        return {'ok': True, 'unclosed': [{
            'id': e.id,
            'title': e.name,
            'phone': e.modryn_customer_phone or '',
            'when': pytz.utc.localize(e.start).astimezone(TZ).strftime('%d.%m %H:%M'),
            'outcome': '',
            'sale_amount': 0.0,
            'sale_items': '',
            'employee_id': e.modryn_employee_id.id or False,
        } for e in events]}

    @http.route('/floor/finish/booking', type='jsonrpc', auth='user')
    def finish_booking(self, event_id, outcome, amount=None, items=None,
                       note=None, force=False):
        """Close a booking with its outcome.

        Deliberately wider than the manager-only walk-in finish (a settled
        decision): the booking's own primary stylist may close it — she was in
        the room — but only a manager may CHANGE an outcome already recorded,
        so force from a non-manager is refused before anything else runs.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        event = request.env['calendar.event'].sudo().browse(int(event_id)).exists()
        if not event or not event.modryn_is_booking:
            return {'error': 'not_found'}
        me = self._my_employee()
        if not self._is_manager():
            if force or not me or event.modryn_employee_id != me:
                return {'error': 'forbidden'}
        # The jsonrpc boundary can carry anything; a junk amount must come
        # back as an error, not a 500 out of float().
        try:
            amount = float(amount or 0.0)
        except (TypeError, ValueError):
            return {'error': 'invalid_amount'}
        if amount < 0:
            return {'error': 'invalid_amount'}

        error = event.modryn_set_outcome(
            outcome, me,
            amount=amount, items=items, note=note, force=bool(force))
        if error:
            return {'error': error}

        board = self._board()
        if outcome == 'sold':
            # The same payload shape as the walk-in finish, so the existing
            # alteration-handoff modal chains with no new plumbing. The partner
            # lookup gives the modal her actual name rather than the booking
            # title's "Fitting: …" prefix.
            partner = None
            if event.modryn_customer_phone:
                partner = request.env['res.partner'].sudo().search(
                    [('phone', '=', event.modryn_customer_phone)], limit=1)
            variants = request.env['product.product'].sudo().search([
                ('product_tmpl_id.is_published', '=', True),
            ])
            board['finished'] = {
                'customer': partner.name if partner else event.name,
                'phone': event.modryn_customer_phone or '',
                'variants': [{
                    'id': v.id,
                    'label': '%s · %s' % (
                        v.product_tmpl_id.name,
                        v.product_template_attribute_value_ids[:1].name or v.name,
                    ),
                } for v in variants],
            }
        return board

    # ------------------------------------------------------ customer profile
    def _customer_payload(self, partner):
        """The profile dict, shaped by role. BUDGET IS DELIBERATELY ABSENT
        from the staff payload — under this codebase's security model (portal
        users, controller auth, sudo reads, plain dicts) omitting the key IS
        the field-level ACL. Do not 'simplify' this into one dict."""
        data = {
            'id': partner.id,
            'name': partner.name,
            'phone': partner.phone or '',
            'wedding_date': (partner.modryn_wedding_date.strftime('%Y-%m-%d')
                             if partner.modryn_wedding_date else ''),
            'party': partner.modryn_party_notes or '',
            'measurements': partner.modryn_measurements or '',
            'notes': partner.modryn_notes or '',
            'category': partner.modryn_category or '',
        }
        if self._is_manager():
            data['budget'] = partner.modryn_budget or 0.0
        return data

    @http.route('/floor/customer', type='jsonrpc', auth='user')
    def customer_profile(self, phone):
        """Look a customer up by phone — the one identifier every card on the
        floor board carries, and the same key bookings use to search-or-create
        partners."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        phone = (phone or '').strip()
        if not phone:
            return {'error': 'not_found'}
        partner = request.env['res.partner'].sudo().search(
            [('phone', '=', phone)], limit=1)
        if not partner:
            # No profile yet — the modal offers to start one on first save.
            return {'ok': True, 'customer': None}
        return {'ok': True, 'customer': self._customer_payload(partner)}

    @http.route('/floor/customer/save', type='jsonrpc', auth='user')
    def customer_save(self, phone, name=None, wedding_date=None, party=None,
                      measurements=None, notes=None, budget=None):
        """Staff write the relationship data; only a manager may touch budget.

        The client never sends a budget key for staff — but a forged payload
        might, so the refusal lives here, not in the UI.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        if budget is not None and not self._is_manager():
            return {'error': 'forbidden'}
        phone = (phone or '').strip()
        if not phone:
            return {'error': 'not_found'}
        Partner = request.env['res.partner'].sudo()
        partner = Partner.search([('phone', '=', phone)], limit=1)
        if not partner:
            # Same search-or-create rule as /book/submit: walk-in brides
            # deserve a profile too, and phone is the identity key.
            partner = Partner.create({'name': (name or phone).strip(), 'phone': phone})

        values = {
            'modryn_party_notes': (party or '').strip(),
            'modryn_measurements': (measurements or '').strip(),
            'modryn_notes': (notes or '').strip(),
        }
        if wedding_date:
            try:
                datetime.strptime(wedding_date, '%Y-%m-%d')
            except (TypeError, ValueError):
                return {'error': 'invalid_date'}
        values['modryn_wedding_date'] = wedding_date or False
        if budget is not None:
            try:
                values['modryn_budget'] = float(budget or 0.0)
            except (TypeError, ValueError):
                return {'error': 'invalid_budget'}
        partner.write(values)
        return {'ok': True, 'customer': self._customer_payload(partner)}
