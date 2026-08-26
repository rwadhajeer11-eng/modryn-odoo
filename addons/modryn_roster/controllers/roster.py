from datetime import datetime, timedelta

from odoo import _, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access

from ..models.shift_slot import next_week_start, week_start
from ..models.shift_template import weekday_selection

_lt = LazyTranslate(__name__)

GROUP_OWNER = 'modryn_staff.group_boutique_owner'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_STAFF = 'modryn_staff.group_boutique_staff'

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
        slots = request.env['modryn.shift.slot'].sudo().modryn_ensure_week(start)
        return [slot._row(employee=employee) for slot in slots]

    # ------------------------------------------------------------------ page
    @http.route('/roster', type='http', auth='user', website=True, sitemap=False)
    def roster(self, week=None, **kw):
        if not access.can_view('roster'):
            return access.deny()
        try:
            offset = int(week or 0)
        except ValueError:
            offset = 0
        # Only the week being planned and the one after it. A grid that can walk
        # backwards invites editing history nobody meant to change.
        offset = max(-1, min(offset, 1))
        start = self._week(offset)
        me = self._my_employee()
        return request.render('modryn_roster.roster_page', {
            'slots': self._grid(start, employee=me),
            'week_start': start,
            'week_end': start + timedelta(days=6),
            'week_offset': offset,
            'is_manager': self._is_manager(),
            'is_owner': self._is_owner(),
            'me': me,
            'staff': request.env['hr.employee'].sudo().search([
                ('modryn_level', 'in', ('manager', 'staff')),
            ]),
        })

    # --------------------------------------------------------------- actions
    def _slot(self, slot_id):
        return request.env['modryn.shift.slot'].sudo().browse(int(slot_id)).exists()

    @http.route('/roster/available', type='jsonrpc', auth='user')
    def toggle_available(self, slot_id, week=0):
        """I can work that one — or, on second thoughts, I can't."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        me = self._my_employee()
        slot = self._slot(slot_id)
        if not me or not slot:
            return {'error': 'not_found'}
        ok, message = request.env['modryn.availability'].sudo().modryn_toggle(me, slot)
        grid = self._grid(self._week(int(week)), employee=me)
        return {'slots': grid, 'error': None if ok else message}

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
    @http.route('/manage/shifts', type='http', auth='user', website=True, sitemap=False)
    def shifts(self, error=None, **kw):
        if not self._is_owner():
            return request.not_found()
        return request.render('modryn_roster.manage_shifts', {
            'templates': request.env['modryn.shift.template'].sudo().with_context(
                active_test=False).search([]),
            'roles': request.env['modryn.staff.role'].sudo().search([]),
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
