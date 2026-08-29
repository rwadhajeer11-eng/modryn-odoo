"""The shift supervisor's screen.

One page answering the questions a manager standing in a boutique actually asks,
and which no existing screen answered together: who is on the floor, since when,
until when they are meant to be, what job each of them does, who each of them is
with, and how many that is.

Read-only about people and their hours - the times come from the attendance rows
the floor's own door writes and from the published rota, never typed here. The
one thing it WRITES is a note about how a visit went, because that is a judgement
only the person watching the room can make and there was nowhere to put it.
"""

from datetime import datetime, time

import pytz

from odoo import _, fields, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from .. import nav
from . import access

_lt = LazyTranslate(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# The staff section, so the owner can grant it to a role - a senior saleswoman
# running a Saturday needs this screen and is not a manager. It is NOT under
# /manage for the same reason: /manage is owner-only by deliberate design.
nav.register('supervisor', '/shift-supervisor', _lt("Shift supervisor"), 15,
             icon='fa-user-circle-o')


def _local(value):
    """A stored naive-UTC datetime as the boutique's wall clock, for printing."""
    return pytz.utc.localize(value).astimezone(TZ) if value else None


class ModrynSupervisor(http.Controller):

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _today(self):
        """Today in the boutique's own day, not the server's.

        A shift that ends at 21:00 local is still today at 19:00 UTC, and a
        supervisor looking at the screen at closing time must not watch the day
        roll over under her.
        """
        return datetime.now(TZ).date()

    def _bounds(self, day):
        """The UTC half-open range covering one LOCAL day."""
        start = TZ.localize(datetime.combine(day, time.min)).astimezone(
            pytz.UTC).replace(tzinfo=None)
        end = TZ.localize(datetime.combine(day, time.max)).astimezone(
            pytz.UTC).replace(tzinfo=None)
        return start, end

    def _expected(self, employee, day):
        """When the rota says she should arrive and leave today.

        Read off PUBLISHED slots only: an unpublished rota is a draft, and
        holding a woman to a time nobody has told her about is not a thing this
        screen should help anybody do.

        A day can carry two shifts for one woman, so this returns the earliest
        start and the latest end - which is what "when should she be here" means
        across a split day.
        """
        slots = request.env['modryn.shift.slot'].sudo().search([
            ('day', '=', day),
            ('published', '=', True),
            ('employee_ids', 'in', employee.id),
        ])
        if not slots:
            return None
        fmt = lambda h: '%02d:%02d' % (int(h), int(round((h - int(h)) * 60)))
        return {
            'from': fmt(min(slots.mapped('start_hour'))),
            'to': fmt(max(slots.mapped('end_hour'))),
            'names': ', '.join(s.modryn_name() for s in slots),
        }

    def _customers_by_employee(self):
        """Who each woman has with her right now, walk-ins and bookings alike.

        Keyed by employee id, with a None key for work nobody has taken - the
        supervisor needs to see that queue too, and a screen that only lists
        assigned customers hides the ones waiting for somebody.
        """
        out = {}
        Queue = request.env['modryn.queue.entry'].sudo()
        for entry in Queue.search([('state', 'in', ('waiting', 'called'))]):
            out.setdefault(entry.modryn_employee_id.id or None, []).append({
                'kind': 'queue',
                'id': entry.id,
                'name': entry.name or '',
                'phone': entry.phone or '',
                'client_type': entry.client_type or '',
                'note': entry.staff_note or '',
                'state': entry.state,
                'rating': entry.modryn_visit_rating or 0,
                'rating_note': entry.modryn_visit_note or '',
                'helpers': entry.modryn_helper_ids.mapped('name'),
            })
        start, end = self._bounds(self._today())
        Event = request.env['calendar.event'].sudo()
        for event in Event.search([
                ('modryn_is_booking', '=', True),
                ('modryn_cancelled_at', '=', False),
                ('start', '>=', fields.Datetime.to_string(start)),
                ('start', '<=', fields.Datetime.to_string(end))]):
            out.setdefault(event.modryn_employee_id.id or None, []).append({
                'kind': 'booking',
                'id': event.id,
                'name': event.name or '',
                'phone': event.modryn_customer_phone or '',
                'client_type': '',
                'note': event.modryn_outcome_note or '',
                'state': 'booking',
                'time': _local(event.start).strftime('%H:%M') if event.start else '',
                'rating': event.modryn_visit_rating or 0,
                'rating_note': event.modryn_visit_note or '',
                'helpers': event.modryn_helper_ids.mapped('name'),
            })
        return out

    @http.route('/shift-supervisor', type='http', auth='user', website=True,
                sitemap=False)
    def supervisor(self, **kw):
        if not access.can_view('supervisor'):
            return access.deny()
        day = self._today()
        start, end = self._bounds(day)
        Attendance = request.env['modryn.shift.attendance'].sudo()
        by_employee = self._customers_by_employee()

        rows = []
        employees = request.env['hr.employee'].sudo().search(
            [('modryn_level', 'in', ('owner', 'manager', 'staff'))], order='name')
        for employee in employees:
            # Every stretch she was on the floor today, not just the open one:
            # a woman who came on, went to lunch and came back is two rows and
            # the supervisor should see both.
            spells = Attendance.search([
                ('employee_id', '=', employee.id),
                ('started_at', '>=', fields.Datetime.to_string(start)),
                ('started_at', '<=', fields.Datetime.to_string(end)),
            ], order='started_at')
            customers = by_employee.get(employee.id, [])
            rows.append({
                'id': employee.id,
                'name': employee.name,
                # Every role she holds. A woman doing two jobs is exactly who a
                # supervisor needs to be able to see at a glance.
                'roles': ' · '.join(employee.modryn_role_ids.mapped('name')),
                'on_floor': bool(employee.modryn_on_shift_since),
                'spells': [{
                    'in': _local(a.started_at).strftime('%H:%M'),
                    'out': _local(a.ended_at).strftime('%H:%M') if a.ended_at else '',
                } for a in spells],
                'expected': self._expected(employee, day),
                'customers': customers,
                'count': len(customers),
            })

        return request.render('modryn_staff.shift_supervisor', {
            'rows': rows,
            'unassigned': by_employee.get(None, []),
            'day_label': day.strftime('%d.%m.%Y'),
            'active_tab': 'supervisor',
        })

    @http.route('/shift-supervisor/rate', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def rate(self, **post):
        """How the visit went, as the person watching the room saw it.

        Recorded against the VISIT and not against the customer: the same bride
        can have a difficult morning and an easy afternoon, and a score that
        follows her forever is a judgement no boutique should be making from one
        appointment.
        """
        if not access.can_view('supervisor'):
            return access.deny()
        kind = post.get('kind')
        try:
            rating = int(post.get('rating') or 0)
        except ValueError:
            rating = 0
        # Clamped rather than refused: the form offers five buttons, so anything
        # else is a hand-made POST and there is nothing to tell a person about.
        rating = max(0, min(5, rating))
        note = (post.get('note') or '').strip()

        model = {'queue': 'modryn.queue.entry', 'booking': 'calendar.event'}.get(kind)
        if not model:
            return request.redirect('/shift-supervisor')
        record = request.env[model].sudo().browse(
            int(post.get('record_id') or 0)).exists()
        if record:
            record.write({
                'modryn_visit_rating': rating,
                'modryn_visit_note': note,
            })
        return request.redirect('/shift-supervisor')
