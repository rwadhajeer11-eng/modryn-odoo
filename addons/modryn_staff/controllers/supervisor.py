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
        """When the rota says she should be here today, one line per shift.

        Read off PUBLISHED slots only: an unpublished rota is a draft, and
        holding a woman to a time nobody has told her about is not a thing this
        screen should help anybody do.

        ONE LINE PER SLOT, not a span across them. Returning the earliest start
        and the latest end made a woman rota'd 09:00-13:00 and 17:00-21:00 read
        as a twelve-hour shift, and the four-hour hole in the Actually column
        beside it then read as her having disappeared for the afternoon. It is
        also the shape that column already uses, so the two can be read against
        each other.
        """
        slots = request.env['modryn.shift.slot'].sudo().search([
            ('day', '=', day),
            ('published', '=', True),
            ('employee_ids', 'in', employee.id),
        ], order='start_hour')
        if not slots:
            return []

        def hhmm(hour):
            # Stored as a float of hours: 9.5 is half past nine. Rounded to the
            # minute rather than truncated, or 17.999999 prints as 17:59.
            minutes = int(round(hour * 60))
            return '%02d:%02d' % (minutes // 60, minutes % 60)

        return [{
            'from': hhmm(slot.start_hour),
            'to': hhmm(slot.end_hour),
            'name': slot.modryn_name(),
        } for slot in slots]

    def _customers_by_employee(self):
        """Who is with whom, split into what is happening and what is coming.

        Two dicts keyed by employee id, each with a None key for work nobody has
        taken - the supervisor needs that queue too, and a screen that lists only
        assigned customers hides the ones waiting for somebody.

        NOW and LATER are separated because the screen was quietly lying about
        both. A walk-in can be assigned to a stylist while still waiting - a
        manager saying "you take this one next" - and she was counted as being
        served. So was every one of today's bookings: measured at 12:23, a bride
        due at 13:00 was listed under "With her now".
        """
        now, later = {}, {}
        stamp = fields.Datetime.now()

        Queue = request.env['modryn.queue.entry'].sudo()
        for entry in Queue.search([('state', 'in', ('waiting', 'called'))]):
            row = {
                'kind': 'queue',
                'id': entry.id,
                'name': entry.name or '',
                'phone': entry.phone or '',
                'client_type': entry.client_type or '',
                'note': entry.staff_note or '',
                'state': entry.state,
                'time': '',
                'rating': entry.modryn_visit_rating or 0,
                'rating_note': entry.modryn_visit_note or '',
                'helpers': entry.modryn_helper_ids.mapped('name'),
            }
            who = entry.modryn_employee_id.id or None
            # Nobody on her: she is waiting for somebody, whatever her state.
            # Assigned but still waiting: she is that stylist's NEXT, not her
            # current - and calling that "with her now" is the fault this split
            # exists to fix.
            if who is None:
                now.setdefault(None, []).append(row)
            elif entry.state == 'called':
                now.setdefault(who, []).append(row)
            else:
                later.setdefault(who, []).append(row)

        start, end = self._bounds(self._today())
        Event = request.env['calendar.event'].sudo()
        for event in Event.search([
                ('modryn_is_booking', '=', True),
                ('modryn_cancelled_at', '=', False),
                ('start', '>=', fields.Datetime.to_string(start)),
                ('start', '<=', fields.Datetime.to_string(end))], order='start'):
            row = {
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
            }
            who = event.modryn_employee_id.id or None
            running = bool(event.start and event.stop
                           and event.start <= stamp < event.stop)
            if running and not event.modryn_outcome:
                now.setdefault(who, []).append(row)
            elif event.start and event.start > stamp:
                later.setdefault(who, []).append(row)
            # An appointment that has finished - its hour gone by, or an outcome
            # already recorded - belongs to neither. It is not happening and it
            # is not coming, and a screen about the shift as it stands should not
            # make a supervisor read past it.
        return now, later

    def _open_calls(self):
        """Every call for help still standing, newest first.

        EVERY one, unlike the floor board, which deliberately shows a stylist
        only the calls that concern her. Whoever is running the shift is the
        person the general bell rings for, and a screen that filtered by
        involvement would have shown her the one thing she cannot help with.
        """
        Call = request.env['modryn.sos.call'].sudo()
        return [{
            'id': c.id,
            'caller': c.caller_id.name or '',
            'target': c.target_id.name or '',
            'where': c._where(),
            'note': c.note or '',
            'state': c.state,
            'acked_by': c.acked_by_id.name or '',
            'escalated': bool(c.escalated_at),
            'at': _local(c.create_date).strftime('%H:%M') if c.create_date else '',
        } for c in Call.search([('state', 'in', ('open', 'acked'))])]

    @http.route('/shift-supervisor/sos/resolve', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def sos_resolve(self, **post):
        """Somebody went and helped. Closes the call from this screen.

        The board has a resolve of its own, reached from the caller's overlay -
        which is the wrong screen for the person who actually answered, and on a
        busy floor is a screen she is not looking at.
        """
        if not access.can_view('supervisor'):
            return access.deny()
        call = request.env['modryn.sos.call'].sudo().browse(
            int(post.get('call_id') or 0)).exists()
        if call and call.state != 'resolved':
            call.modryn_resolve()
        return request.redirect('/shift-supervisor')

    @http.route('/shift-supervisor', type='http', auth='user', website=True,
                sitemap=False)
    def supervisor(self, **kw):
        if not access.can_view('supervisor'):
            return access.deny()
        day = self._today()
        start, end = self._bounds(day)
        Attendance = request.env['modryn.shift.attendance'].sudo()
        by_employee, coming = self._customers_by_employee()

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
                } for a in spells[-6:]],
                # One sentence rather than a number with a loose word beside it.
                # Split across QWeb nodes it extracts as the bare msgid
                # "earlier", which tells a translator nothing about what is
                # being counted and gives her nowhere to move the number to.
                'earlier': (
                    _("%(count)s earlier today.") % {'count': len(spells) - 6}
                    if len(spells) > 6 else ''),
                'expected': self._expected(employee, day),
                'customers': customers,
                'count': len(customers),
                # Hers, but not yet: the walk-in a manager has put aside for her
                # and the appointments still to come. Shown without the rating
                # form - there is nothing to judge about a visit that has not
                # happened.
                'later': coming.get(employee.id, []),
            })

        unassigned = by_employee.get(None, [])
        # Coming later with nobody on them. Counted rather than listed: the
        # floor board's own appointments panel is where a manager assigns them,
        # and a second copy of that list here would be a screen competing with
        # itself. The number is the part she cannot get anywhere else.
        later_loose = len(coming.get(None, []))
        return request.render('modryn_staff.shift_supervisor', {
            'calls': self._open_calls(),
            'rows': rows,
            # The first handful and a count, never the whole line. A tenant
            # holding 99 unclaimed walk-ins turned this panel into the entire
            # page and pushed every worker's card off the bottom - the one
            # thing a supervisor opens this screen to read.
            'unassigned': unassigned[:12],
            'unassigned_total': len(unassigned),
            # One sentence, so a translator can put the numbers where her
            # language puts them. Split across QWeb text nodes it extracted as
            # the fragment "Showing the first" and Hebrew rendered the count
            # stranded behind the noun.
            'unassigned_note': (
                _("Showing the first %(shown)s of %(total)s.") % {
                    'shown': 12, 'total': len(unassigned)}
                if len(unassigned) > 12 else ''),
            'later_note': (
                _("%(count)s more later today with nobody on them yet.")
                % {'count': later_loose} if later_loose else ''),
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
