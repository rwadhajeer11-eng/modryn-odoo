from datetime import date, datetime, timedelta

import werkzeug.urls
from psycopg2 import IntegrityError

from odoo import _, fields, http
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools import mute_logger
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access
# Reused, not re-implemented. This module already carries two private copies of
# an hour formatter; a third would be the one that drifts.
from odoo.addons.modryn_staff.controllers.manage import (
    _clock_to_float, _fmt_hour, _to_date)

from ..models.roster_week import _from_utc, _to_utc, window_days
from ..models.shift_slot import next_week_start, week_start
from ..models.shift_template import (
    SHIFT_TYPE_ORDER, active_shift_types, set_active_shift_types,
    shift_type_selection, weekday_name, weekday_selection)

_lt = LazyTranslate(__name__)

GROUP_OWNER = 'modryn_staff.group_boutique_owner'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_STAFF = 'modryn_staff.group_boutique_staff'

# How far the schedule may be READ in either direction. A year: far enough to
# look back over what the boutique has actually worked and forward over what is
# coming, and short enough that the arrows have an end rather than walking into
# 2043 one press at a time.
#
# Reading only. Nothing here decides what may be FILLED - can_edit does that,
# and it refuses every week except the one whose submission window is open.
WEEKS_EITHER_WAY = 52

nav.register('shifts', '/manage/shifts', _lt("Shifts"), 60, 'manage', 'fa-calendar-o')


class ModrynRoster(http.Controller):
    """Next week's rota: staff offer, the manager fills, everyone sees it.

    Same security posture as the rest of the staff layer — portal users have no
    ORM access to hr.employee, so every route checks its group here and reads
    through sudo(), handing templates plain dicts.
    """

    # ---------------------------------------------------------------- helpers
    def _user(self):
        user = request.env.user
        return None if user._is_public() else user

    def _is_staff(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_STAFF)

    def _is_manager(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_MANAGER)

    def _is_owner(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_OWNER)

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _week(self, offset=0):
        """Which week the grid is showing. 0 = next week, the one being planned."""
        return next_week_start() + timedelta(days=7 * offset)

    def _grid(self, start, employee=None):
        """Every real shift in the week, as the manager's assign side reads it.

        Still keyed and returned as `slots`, deliberately. Two things outside
        this file depend on that: the load test classifies a legitimate refusal
        by Array.isArray(result.slots), and the gate asserts on this route by
        name. Only the PARAMS changed, never the path or the response key.
        """
        Availability = request.env['modryn.availability'].sudo()
        week_map = Availability.modryn_week_map(start)
        slots = request.env['modryn.shift.slot'].sudo().modryn_ensure_week(start)
        return [slot._row(employee=employee,
                          available_ids=week_map.get((slot.day, slot._shift_type()), []))
                for slot in slots]

    def _planner_days(self, start):
        """The week as the person BUILDING it reads it.

        One entry per day, holding the real shifts on that day with who offered
        each and who is on it. _days() answers the other side's question - "did
        I offer this cell" - and carries no names at all, which is the whole of
        what a planner needs.
        """
        rows = self._grid(start)
        days = []
        for offset in range(7):
            day = start + timedelta(days=offset)
            key = day.strftime('%Y-%m-%d')
            days.append({
                'date': key,
                'label': '%s %s' % (weekday_name(day), day.strftime('%d.%m')),
                'slots': [row for row in rows if row['day'] == key],
            })
        return days

    def _days(self, start, employee=None):
        """The week as seven days of three cells - the thing she actually taps.

        Built from WEEKDAY_ORDER x the parts this boutique RUNS, and not from
        the shift templates: a cell exists because Friday evening is a thing,
        not because the boutique has already written a template for it. Five
        templates used to mean five things to press.

        The parts are the manager's setting now. A shop that never opens an
        evening was asking its team to leave a row of seven boxes empty every
        week; untick it on משמרות and the row goes.

        Nested day -> cells rather than a flat list of twenty-one, so the phone
        breakpoint (one day per row, three cells across) is pure CSS with no
        second markup path.
        """
        parts = active_shift_types(request.env)
        Availability = request.env['modryn.availability'].sudo()
        week_map = Availability.modryn_week_map(start)
        slots = request.env['modryn.shift.slot'].sudo().modryn_ensure_week(start)

        by_cell = {}
        for slot in slots:
            by_cell.setdefault((slot.day, slot._shift_type()), []).append(slot)

        type_labels = dict(shift_type_selection())
        days = []
        for offset in range(7):
            day = start + timedelta(days=offset)
            cells = []
            for code in parts:
                ids = week_map.get((day, code), [])
                here = by_cell.get((day, code), [])
                cells.append({
                    'day': day.strftime('%Y-%m-%d'),
                    'shift_type': code,
                    'type_label': type_labels.get(code, code),
                    'mine': bool(employee and employee.id in ids),
                    'offers': len(ids),
                    # The real shifts sitting under this cell, for the manager.
                    # A day-and-part may legitimately carry two of them.
                    'slot_ids': [slot.id for slot in here],
                    'has_shift': bool(here),
                    'published': any(slot.published for slot in here),
                })
            days.append({
                'date': day.strftime('%Y-%m-%d'),
                'label': day.strftime('%d.%m'),
                'weekday': weekday_name(day),
                'cells': cells,
            })
        return days

    # ------------------------------------------------------------------ page
    @http.route('/roster', type='http', auth='user', website=True, sitemap=False)
    def roster(self, week=None, error=None, warning=None, **kw):
        if not access.can_view('roster'):
            return access.deny()
        try:
            offset = int(week or 0)
        except ValueError:
            offset = 0
        # A YEAR EACH WAY. It used to be clamped to [-1, 1] on the reasoning
        # that "a grid that can walk backwards invites editing history nobody
        # meant to change" - but the walking was never what protected the
        # history. can_edit is: it refuses any week already being worked
        # (offset < 0), refuses a frozen one, and refuses one whose submission
        # window is not open, and a future week's window opens the week before
        # it. So week +40 renders locked because it is not open yet, and week
        # -40 renders locked because it is gone. Measured, both.
        #
        # What the clamp actually cost was READING: the boutique could not look
        # back at the rotas it has worked or forward at what is coming, which is
        # most of what anybody wants this page for outside the one week they are
        # answering for.
        offset = max(-WEEKS_EITHER_WAY, min(offset, WEEKS_EITHER_WAY))
        start = self._week(offset)
        me = self._my_employee()
        # Not called "week": that is this route's own parameter, and shadowing
        # it works here only because offset happens to be read first.
        week_row = request.env['modryn.roster.week'].sudo().modryn_for(start)
        opens, closes = week_row._window()
        is_open = week_row.modryn_is_open()
        rule = week_row._default_window()
        Submission = request.env['modryn.roster.submission'].sudo()

        # Her own answer for this week: the note she wrote and whether she has
        # sent it. Absent is NOT the same as "available for nothing" — a manager
        # has to be able to tell a team member who cannot work from one who has
        # not looked yet, and before this row existed both were an empty set.
        mine = Submission.modryn_for(start, me) if me else None

        # ONE truth for "may she still change this week", and three separate
        # reasons why not. The page used to disable the cells on window_open
        # alone, which is only one of the three: a published week left every
        # cell looking pressable and refused every press, and the week already
        # being worked did the same. From the chair that is indistinguishable
        # from the feature being broken - which is exactly what it was reported
        # as.
        frozen = request.env['modryn.roster.week'].sudo().modryn_is_frozen(start)
        # offset < 0 is the week she is standing in. Its rota was built and sent
        # days ago; nothing she ticks there can change what she is working now.
        is_current_week = offset < 0
        can_edit = bool(me) and is_open and not frozen and not is_current_week
        # "Closed" is two different sentences. A window that has not opened yet
        # is a date to come back on; one that has shut is a deadline she has
        # missed. The page printed the first for both, so a woman looking at a
        # window that ended on Sunday was told it "opens" on Sunday - which
        # reads as the site having the wrong date, not as her being late.
        window_passed = bool(closes) and closes <= fields.Datetime.now()
        if is_current_week:
            lock_reason = 'current'
        elif frozen:
            lock_reason = 'published'
        elif not is_open:
            lock_reason = 'passed' if window_passed else 'closed'
        else:
            lock_reason = None

        rows = self._grid(start, employee=me)
        # What she actually offered, as a sentence rather than a grid. When the
        # table locks she still has to be able to READ her own answer - "you
        # cannot change this" and "you cannot see this" are not the same
        # promise, and only the first one is the deadline.
        my_days = self._days(start, employee=me)
        my_picks = [{
            'weekday': d['weekday'],
            'label': d['label'],
            'parts': [c['type_label'] for c in d['cells'] if c['mine']],
        } for d in my_days if any(c['mine'] for c in d['cells'])]

        return request.render('modryn_roster.roster_page', {
            'slots': rows,
            # The seven-by-three grid she taps. Kept ALONGSIDE `slots` rather
            # than replacing it: `slots` is the manager's assign side, and it is
            # also the response key the load test classifies refusals by.
            'days': my_days,
            # The row headings down the side of the grid. Only the parts the
            # boutique runs, so the table has as many rows as it has answers to
            # ask for.
            'shift_rows': [(code, dict(shift_type_selection())[code])
                           for code in active_shift_types(request.env)],
            'window_days': window_days(),
            # Whether the window may be set at all for the week on screen, and
            # the week it would serve — stated in the panel rather than left to
            # be worked out from a weekday name and an anchor rule.
            'window_settable': offset >= self.PLANNABLE_FROM,
            # Pre-formatted for <input type="time">, so nobody has to think of
            # half past ten as 10.5 — the same reasoning the opening-hours form
            # already applies, and the reason _fmt_hour is shared rather than
            # rewritten here.
            'window_rule': {
                'open_weekday': str(rule[0][0]), 'open_time': _fmt_hour(rule[0][1]),
                'close_weekday': str(rule[1][0]), 'close_time': _fmt_hour(rule[1][1]),
            },
            'window_overridden': bool(week_row.opens_at and week_row.closes_at),
            'error': error,
            'warning': warning,
            'week_start': start,
            'week_end': start + timedelta(days=6),
            'week_offset': offset,
            'is_manager': self._is_manager(),
            'is_owner': self._is_owner(),
            'me': me,
            'window_open': is_open,
            # The template asks can_edit, never window_open, for anything that
            # disables a control.
            'can_edit': can_edit,
            'lock_reason': lock_reason,
            'week_frozen': frozen,
            'my_picks': my_picks,
            # _from_utc on all three, and it is a bug FIX, not a tidy-up:
            # _window() and submitted_at are naive UTC and the page strftime'd
            # them raw, so the shipped Saturday 21:00 deadline printed as
            # "18:00". Invisible while nothing wrote the window; the moment a
            # manager types 21:00 and reads 18:00 back, the only sane reading
            # is that it did not save.
            'window_opens': _from_utc(opens),
            'window_closes': _from_utc(closes),
            'my_note': (mine.note or '') if mine else '',
            'my_submitted_at': _from_utc(mine.submitted_at) if (
                mine and mine.submitted_at) else None,
            # Who has answered, for the manager. Only for the week on screen.
            'submissions': [{
                'employee_id': s.employee_id.id,
                'name': s.employee_id.name,
                'note': s.note or '',
                'submitted_at': _from_utc(s.submitted_at) if s.submitted_at else None,
            } for s in Submission.search([('week_start', '=', start)])
                if s.employee_id.active],
            'staff': request.env['hr.employee'].sudo().search([
                ('modryn_level', 'in', ('manager', 'staff')),
            ]),
        })

    # ------------------------------------------------------ submission window
    @http.route('/roster/send', type='jsonrpc', auth='user')
    def send_availability(self, week=0, note=None):
        """"That's my week." The ticks were already saved; this is the answer.

        Kept separate from the per-slot toggle on purpose. The toggle is a draft
        — she can change her mind while she thinks — and this is the moment she
        tells the manager to build on it.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        me = self._my_employee()
        if not me:
            return {'error': 'not_found'}
        if int(week) < self.PLANNABLE_FROM:
            return {'error': 'past_week',
                    'message': _("This week is already being worked - you can only"
                                 " answer for the week being planned.")}
        start = self._week(int(week))
        week_row = request.env['modryn.roster.week'].sudo().modryn_for(start)
        # Server-side, because a closed window that only hides a button is not a
        # deadline.
        if request.env['modryn.roster.week'].sudo().modryn_is_frozen(start):
            return {'error': 'published',
                    'message': _("That week is already published - ask your manager"
                                 " to change it.")}
        if not week_row.modryn_is_open():
            opens, closes = week_row._window()
            if closes <= fields.Datetime.now():
                return {'error': 'window_closed',
                        'message': _("Answers for this week closed %s.",
                                     _from_utc(closes).strftime('%d.%m %H:%M'))}
            return {'error': 'window_closed',
                    'message': _("Answers for this week open %s.",
                                 _from_utc(opens).strftime('%d.%m %H:%M'))}
        submission = request.env['modryn.roster.submission'].sudo().modryn_for(start, me)
        submission.modryn_send(note=(note or '').strip())
        return {'ok': True, 'submitted_at': fields.Datetime.to_string(submission.submitted_at)}

    # ------------------------------------------------ when the team may answer
    #
    # Two plain HTML forms, not jsonrpc. The jsonrpc route that used to live
    # here had zero callers anywhere in the repo, could only set ONE week's
    # absolute datetimes (never the recurring rule anybody actually wants), and
    # read its input with fields.Datetime.to_datetime — which treats a Jerusalem
    # wall clock as if it were UTC and stored it two or three hours out.
    #
    # Forms also buy immunity from a trap this page has already fallen into
    # once: roster.js binds ONE delegated listener on .modryn_roster_grid, so a
    # jsonrpc control rendered outside that container renders perfectly and then
    # silently does nothing when pressed. A form needs no listener at all.

    # The submission window only means something for a week nobody has worked
    # yet. Offset -1 is the CURRENT week - the rota being stood right now - so a
    # window set there can only ever describe a deadline that has already gone.
    # The page showed the control anyway, and answered with a window entirely in
    # the past, which reads as the feature being broken rather than as the
    # question being the wrong one to ask.
    PLANNABLE_FROM = 0

    def _refuse_past_week(self, offset):
        """None if this week may still be planned, otherwise the refusal.

        ONE helper called from both window routes, rather than the same six
        lines pasted into each. The paste is not hypothetical: it is exactly how
        this shipped the first time — both copies landed in window_rule, the
        second unreachable, and window_week was left with no guard at all. A
        manager could still write a submission window onto the week her team was
        already standing, and the only visible symptom would have been
        availability re-opening on a rota in progress.

        It also keeps the SENTENCE identical across both routes, which is the
        rule this module already states elsewhere: two wordings for one rule
        teach a manager there are two rules.
        """
        if offset < self.PLANNABLE_FROM:
            return self._window_redirect(offset, error=_(
                "That week is already being worked — set the times for a week "
                "that is still being planned."))
        return None

    def _window_redirect(self, offset, error=None, warning=None):
        """Back to the screen these forms are actually on.

        /roster until the window controls moved off it - which made a saved rule
        bounce the manager onto the workers' availability page, with her own
        screen and any error message left behind on it.
        """
        url = '/manage/shifts?week=%d' % offset
        if error:
            url += '&error=%s' % werkzeug.urls.url_quote(error)
        if warning:
            url += '&warning=%s' % werkzeug.urls.url_quote(warning)
        return request.redirect(url)

    @http.route('/manage/shifts/parts', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def shifts_parts(self, **post):
        """Which parts of the day this boutique runs.

        The MANAGER's, like standing the rota - she is the one who knows the
        shop never opens on a Tuesday evening. Defining what a shift IS stays
        the owner's, further down that page; this only says which of the three
        parts the team is asked about at all.
        """
        if not self._is_manager():
            return request.not_found()
        kept = set_active_shift_types(
            request.env, request.httprequest.form.getlist('parts'))
        # Nothing to say when it took: the grid below is the answer. The refusal
        # is the case worth a word, because a form that silently keeps what it
        # had reads as a form that did not save.
        target = '/manage/shifts'
        if not kept or set(kept) != set(request.httprequest.form.getlist('parts')):
            target += '?error=%s' % werkzeug.urls.url_quote(
                _("A schedule needs at least one part of the day."))
        return request.redirect(target)

    @http.route('/roster/window/rule', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def window_rule(self, **post):
        """The recurring rule: which day, from what time to what time.

        MANAGER-gated, not owner-gated. The levels nest — owner implies shift
        manager implies staff — so gating on manager includes the owner for
        free, while gating on owner would lock out the shift manager, who is
        precisely the person this control is for.
        """
        if not self._is_manager():
            return request.not_found()
        try:
            offset = max(-1, min(int(post.get('week') or 0), 1))
        except ValueError:
            offset = 0
        refused = self._refuse_past_week(offset)
        if refused:
            return refused

        valid_days = {code for code, _label in window_days()}
        open_wd = (post.get('open_weekday') or '').strip()
        close_wd = (post.get('close_weekday') or '').strip()
        if open_wd not in valid_days or close_wd not in valid_days:
            return self._window_redirect(offset, error=_("Please choose a day"))

        open_hour = _clock_to_float(post.get('open_time'))
        close_hour = _clock_to_float(post.get('close_time'))
        if open_hour is None or close_hour is None:
            return self._window_redirect(
                offset, error=_("Please enter an opening and a closing time"))

        # Ordering is a position on the RUNWAY, not a bare hour comparison.
        # Friday 22:00 -> Saturday 06:00 is a perfectly good window and a bare
        # comparison rejects it; the shipped Thursday 09:00 -> Saturday 21:00
        # only passes such a check by luck.
        if (int(close_wd) * 24 + close_hour) <= (int(open_wd) * 24 + open_hour):
            # Deliberately the SAME sentence the model raises. Two wordings for
            # one rule teach a manager there are two rules.
            return self._window_redirect(
                offset, error=_("Submissions have to close after they open."))

        Week = request.env['modryn.roster.week'].sudo()
        landed = Week.modryn_set_default_window(
            open_wd, open_hour, close_wd, close_hour)
        # Read back and compare. _parse_window answers anything unparseable with
        # the shipped default and says nothing, so without this she would be
        # shown Thursday 09:00 and read it as "my save did not take".
        if not landed or landed != ((int(open_wd), open_hour),
                                    (int(close_wd), close_hour)):
            return self._window_redirect(offset, error=_("Could not save that window"))

        # Having saved, say what it means for the week she is looking at. If
        # both edges are already behind us, nobody can answer for that week and
        # nothing else on the page would ever tell her so.
        week_row = Week.modryn_for(self._week(offset))
        opens, closes = week_row._window()
        if fields.Datetime.now() >= closes:
            return self._window_redirect(offset, warning=_(
                "Saved. That time has already gone for the week of %s — nobody can "
                "answer for it. Set times for just this week below to reopen it."
            ) % week_row.week_start.strftime('%d.%m'))
        return self._window_redirect(offset)

    @http.route('/roster/window/week', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def window_week(self, **post):
        """Just this week — a holiday, a team away day."""
        if not self._is_manager():
            return request.not_found()
        try:
            offset = max(-1, min(int(post.get('week') or 0), 1))
        except ValueError:
            offset = 0
        # ABOVE the clear branch deliberately: clearing writes and returns ahead
        # of every other check in this method, so a guard placed any lower would
        # still wipe the current week's window on its way past.
        refused = self._refuse_past_week(offset)
        if refused:
            return refused
        start = self._week(offset)
        week_row = request.env['modryn.roster.week'].sudo().modryn_for(start)

        # Clearing is always legal and skips every check below: it puts the week
        # back on the recurring rule, which is by definition a valid state.
        if post.get('clear'):
            week_row.write({'opens_at': False, 'closes_at': False})
            return self._window_redirect(offset)

        opens_date = _to_date(post.get('opens_date'))
        closes_date = _to_date(post.get('closes_date'))
        opens_hour = _clock_to_float(post.get('opens_time'))
        closes_hour = _clock_to_float(post.get('closes_time'))
        if not opens_date or not closes_date or opens_hour is None or closes_hour is None:
            return self._window_redirect(
                offset, error=_("Please enter a date and a time for both."))

        # A window for a week has to live near that week. Without this a typo'd
        # year sets a window nobody will ever be inside, and the only symptom is
        # that the team quietly cannot answer.
        if not all(start - timedelta(days=13) <= d <= start + timedelta(days=6)
                   for d in (opens_date, closes_date)):
            return self._window_redirect(
                offset, error=_("Those dates aren't near the week you're planning."))

        opens_at = _to_utc(opens_date, opens_hour)
        closes_at = _to_utc(closes_date, closes_hour)
        if closes_at <= opens_at:
            return self._window_redirect(
                offset, error=_("Submissions have to close after they open."))

        # Both edges or neither: _window() falls back PER EDGE, so a half-set
        # week silently mixes one override edge with one rule edge.
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                week_row.write({'opens_at': opens_at, 'closes_at': closes_at})
        except (ValidationError, IntegrityError):
            return self._window_redirect(
                offset, error=_("Submissions have to close after they open."))
        return self._window_redirect(offset)

    # --------------------------------------------------------------- actions
    def _slot(self, slot_id):
        return request.env['modryn.shift.slot'].sudo().browse(int(slot_id)).exists()

    @http.route('/roster/available', type='jsonrpc', auth='user')
    def toggle_available(self, day=None, shift_type=None, week=0, slot_id=None):
        """I can work that one — or, on second thoughts, I can't.

        Addressed by DAY and PART OF DAY now, not by slot id. That is the whole
        point of the grid: she can offer Friday evening before the boutique has
        invented a Friday evening shift, and a slot id cannot name a cell that
        has no slot behind it.

        The route PATH and the `slots` response key are deliberately unchanged.
        The gate asserts on this path by name, and the load test tells a
        legitimate refusal from a real failure by looking for an array under
        `slots` — rename either and both go quietly wrong rather than red.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        me = self._my_employee()
        if not me:
            return {'error': 'not_found'}
        start = self._week(int(week))

        # slot_id is still accepted, so a page already open in somebody's hand
        # when this ships does not start failing mid-session. It is resolved to
        # the day-and-part it stands for and then forgotten.
        if day is None and slot_id is not None:
            slot = self._slot(slot_id)
            if not slot:
                return {'error': 'not_found'}
            day, shift_type = slot.day, slot._shift_type()
        if not day or shift_type not in dict(shift_type_selection()):
            return {'error': 'not_found'}
        if not isinstance(day, date):
            day = fields.Date.to_date(day)
        # The cell must belong to the week it claims. Without this she could
        # post any date at all and hold offers for weeks no page ever shows her.
        if not day or week_start(day) != start:
            return {'error': 'not_found'}

        # The deadline binds the TICKS, not only the Send button. Guarding only
        # the send would leave her able to withdraw a shift after the manager had
        # already counted her into it — which is the exact thing a closing time
        # exists to stop.
        # The week she is STANDING in. Its rota went out days ago, so a tick
        # here could only ever be an offer for a shift that has already been
        # filled - and worse, one she might read back as a change she made.
        if int(week) < self.PLANNABLE_FROM:
            return {'error': 'past_week',
                    'message': _("This week is already being worked - you can only"
                                 " answer for the week being planned.")}

        week_row = request.env['modryn.roster.week'].sudo().modryn_for(start)
        if not week_row.modryn_is_open():
            # A message, not a bare code. This route answered with `error` and
            # NOTHING to read, so a refused press looked exactly like a press
            # that did nothing at all - which is how "it just doesn't work" gets
            # reported for a rule working perfectly.
            opens, closes = week_row._window()
            if closes <= fields.Datetime.now():
                return {'error': 'window_closed',
                        'message': _("Answers for this week closed %s.",
                                     _from_utc(closes).strftime('%d.%m %H:%M'))}
            return {'error': 'window_closed',
                    'message': _("Answers for this week open %s.",
                                 _from_utc(opens).strftime('%d.%m %H:%M'))}
        ok, code, message = request.env['modryn.availability'].sudo().modryn_toggle(
            me, day, shift_type)
        return {
            'slots': self._grid(start, employee=me),
            'days': self._days(start, employee=me),
            # `code` is the stable string a test can match on; `message` is the
            # translated sentence a person reads. A Hebrew sentence can never go
            # on a load test's known-refusals list, which is why there are two.
            'error': None if ok else code,
            'message': None if ok else message,
        }

    @http.route('/roster/assign', type='jsonrpc', auth='user')
    def assign(self, slot_id, employee_id, working, week=0):
        if not self._is_manager():
            return {'error': 'forbidden'}
        slot = self._slot(slot_id)
        employee = request.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        if not slot or not employee:
            return {'error': 'not_found'}
        slot.modryn_set_working(employee, bool(working))
        return {'slots': self._grid(self._week(int(week)), employee=self._my_employee())}

    @http.route('/roster/publish', type='jsonrpc', auth='user')
    def publish(self, week=0):
        """Publish the whole week at once — a half-published rota is a rumour."""
        if not self._is_manager():
            return {'error': 'forbidden'}
        start = self._week(int(week))
        request.env['modryn.shift.slot'].sudo().search(
            [('week_start', '=', start)]).modryn_publish()
        return {'slots': self._grid(start, employee=self._my_employee())}

    # ------------------------------------------------------- shift templates
    def _shift_type_from(self, post, fallback='morning'):
        """Validated against the field's own selection, never trusted raw.

        A junk value here would be invisible rather than loud: a part of the day
        that does not exist can never match a cell, so the shift would simply
        stop appearing on the grid with nothing logged.
        """
        value = (post.get('shift_type') or '').strip()
        return value if value in dict(shift_type_selection()) else fallback

    @http.route('/manage/shifts', type='http', auth='user', website=True, sitemap=False)
    def shifts(self, error=None, warning=None, **kw):
        # A MANAGER always, plus whoever the owner grants it to.
        #
        # This screen now carries the week's rota, and standing one is a shift
        # manager's job by definition - an owner-only gate kept the person who
        # builds the rota off the screen that builds it. A named exception
        # rather than loosening the whole bottom row: everything else down there
        # is the boutique's administration and stays behind a grant. Defining
        # and archiving the shift templates themselves is still the owner's, and
        # the page hides that half from a manager.
        if not (access.is_manager() or access.can_view('shifts')):
            return access.deny()
        me = self._my_employee()
        # Which week is being planned. Offset 0 is the one the team is
        # answering for; the manager can look ahead with ?week=.
        try:
            offset = int(kw.get('week') or 0)
        except (TypeError, ValueError):
            offset = 0
        offset = max(self.PLANNABLE_FROM, offset)
        start = self._week(offset)
        week_row = request.env['modryn.roster.week'].sudo().modryn_for(start)
        opens, closes = week_row._window()
        # The standing rule, which moved here from the roster page. Built the
        # same way it was built there, off the same _default_window - a second
        # derivation would be a second answer to "what is the rule".
        rule = week_row._default_window()
        return request.render('modryn_roster.manage_shifts', {
            # Who has sent their week, which moved here from the roster page.
            # The question a manager asks in the same breath as filling the rota
            # in - and the one thing on that page she could not get on this one.
            # Same builder as before: only the week on screen, and an archived
            # woman is not chased.
            'submissions': [{
                'employee_id': sub.employee_id.id,
                'name': sub.employee_id.name,
                'note': sub.note or '',
                'submitted_at': (_from_utc(sub.submitted_at)
                                 if sub.submitted_at else None),
            } for sub in request.env['modryn.roster.submission'].sudo().search(
                [('week_start', '=', start)]) if sub.employee_id.active],
            # Which parts of the day the boutique runs, and which it has
            # ticked. Both, because the form draws every part there is and marks
            # the ones in use - a list of only the active ones cannot offer the
            # one she wants to add back.
            'all_parts': shift_type_selection(),
            'active_parts': active_shift_types(request.env),
            # ---- when the team may answer ------------------------------
            # The warning the window routes can send. Accepted and DRAWN here
            # because they redirect here now: without it, "that time has already
            # gone, nobody can answer" would be saved, redirected, and shown
            # nowhere - the manager reads a blank save and never learns the week
            # is shut.
            'warning': warning,
            'window_days': window_days(),
            # Whether it may be set for the week on screen at all: a week
            # already being worked has nothing left to answer for.
            'window_settable': offset >= self.PLANNABLE_FROM,
            # Pre-formatted for <input type="time">, so nobody has to think of
            # half past ten as 10.5.
            'window_rule': {
                'open_weekday': str(rule[0][0]), 'open_time': _fmt_hour(rule[0][1]),
                'close_weekday': str(rule[1][0]), 'close_time': _fmt_hour(rule[1][1]),
            },
            # ---- standing the week -------------------------------------
            'is_manager': self._is_manager(),
            # Defining what shifts EXIST stays the owner's. A manager stands
            # next week out of the shifts the boutique runs; inventing a new one
            # is a decision about the business. The routes below already refuse
            # her - this is so the page does not offer her buttons that 404.
            'is_owner': self._is_owner(),
            'week_offset': offset,
            'week_start': start,
            'week_days': self._planner_days(start),
            'window_opens': opens,
            'window_closes': closes,
            'window_open_now': week_row.modryn_is_open(),
            'week_published': request.env['modryn.roster.week'].sudo(
                ).modryn_is_frozen(start),
            'templates': request.env['modryn.shift.template'].sudo().with_context(
                active_test=False).search([]),
            'roles': request.env['modryn.staff.role'].sudo().search([]),
            # THE reason a boutique had no night shift anywhere: this page never
            # offered the choice, so every shift ever made here took the field's
            # default of 'morning', and an owner could not correct a mislabelled
            # evening one from any screen at all. Rendered from the selection
            # function so the options cannot drift from what the model accepts.
            'shift_types': shift_type_selection(),
            # The label list the inline edit form renders its day picker from,
            # so the options cannot drift from the field's own selection.
            'weekdays': weekday_selection(),
            'error': error,
            'active_tab': 'shifts',
        })

    @http.route('/manage/shifts/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def shifts_new(self, **post):
        if not self._is_owner():
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/shifts?error=%s' % _("Please enter a name"))
        try:
            start = float(post.get('start_hour') or 10)
            end = float(post.get('end_hour') or 18)
        except ValueError:
            return request.redirect('/manage/shifts?error=%s' % _("Please enter valid hours"))
        if end <= start:
            return request.redirect(
                '/manage/shifts?error=%s' % _("A shift has to end after it starts"))
        Template = request.env['modryn.shift.template'].sudo()
        weekday = post.get('weekday') or '6'
        if Template.with_context(active_test=False).search_count(
                [('name', '=ilike', name), ('weekday', '=', weekday)]):
            return request.redirect('/manage/shifts?error=%s' % _("That shift already exists"))
        Template.create({
            'name': name, 'weekday': weekday,
            'shift_type': self._shift_type_from(post),
            'start_hour': start, 'end_hour': end,
        })
        return request.redirect('/manage/shifts')

    @http.route('/manage/shifts/edit/<int:template_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def shifts_edit(self, template_id, **post):
        """Rename a shift, or move its day or hours.

        Until now a typo in a shift name could only be fixed by archiving the row
        and typing a new one — which orphaned its coverage targets and left a
        dead row in the list forever.

        Same validation as shifts_new, and deliberately the same wording: two
        different messages for one rule teach the owner there are two rules.
        """
        if not self._is_owner():
            return request.not_found()
        Template = request.env['modryn.shift.template'].sudo().with_context(
            active_test=False)
        template = Template.browse(template_id).exists()
        if not template:
            return request.redirect('/manage/shifts')

        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/shifts?error=%s' % _("Please enter a name"))
        try:
            start = float(post.get('start_hour') or template.start_hour)
            end = float(post.get('end_hour') or template.end_hour)
        except ValueError:
            return request.redirect('/manage/shifts?error=%s' % _("Please enter valid hours"))
        if end <= start:
            return request.redirect(
                '/manage/shifts?error=%s' % _("A shift has to end after it starts"))
        weekday = post.get('weekday') or template.weekday
        # Excluding self: without it, saving a row without renaming it reports
        # "that shift already exists" against itself and the edit never lands.
        if Template.search_count([('id', '!=', template.id),
                                  ('name', '=ilike', name),
                                  ('weekday', '=', weekday)]):
            return request.redirect('/manage/shifts?error=%s' % _("That shift already exists"))
        template.write({
            'name': name, 'weekday': weekday,
            'shift_type': self._shift_type_from(post, template.shift_type),
            'start_hour': start, 'end_hour': end,
        })
        return request.redirect('/manage/shifts')

    @http.route('/manage/shifts/archive/<int:template_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def shifts_archive(self, template_id, **post):
        if not self._is_owner():
            return request.not_found()
        template = request.env['modryn.shift.template'].sudo().with_context(
            active_test=False).browse(template_id).exists()
        if template:
            # Archive, never delete: slots already generated point at this row,
            # and a published week must keep reading correctly.
            template.active = not template.active
        return request.redirect('/manage/shifts')

    @http.route('/manage/shifts/target', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def shifts_target(self, **post):
        """How many of each role this shift needs. Zero removes the target."""
        if not self._is_owner():
            return request.not_found()
        Template = request.env['modryn.shift.template'].sudo()
        Target = request.env['modryn.shift.target'].sudo()
        template = Template.browse(int(post.get('template_id') or 0)).exists()
        role = request.env['modryn.staff.role'].sudo().browse(
            int(post.get('role_id') or 0)).exists()
        if not template or not role:
            return request.redirect('/manage/shifts')
        try:
            required = int(post.get('required') or 0)
        except ValueError:
            required = 0
        existing = Target.search([
            ('template_id', '=', template.id), ('role_id', '=', role.id)], limit=1)
        if required <= 0:
            existing.unlink()
        elif existing:
            existing.required = required
        else:
            Target.create({
                'template_id': template.id, 'role_id': role.id, 'required': required})
        return request.redirect('/manage/shifts')
