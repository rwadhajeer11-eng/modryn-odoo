from odoo import _, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access

_lt = LazyTranslate(__name__)

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'

nav.register('checklists', '/manage/checklists', _lt("Checklists"), 70, 'manage', 'fa-check-square-o')


class ModrynOpsTasks(http.Controller):
    """Ticking, reopening and (for the owner) defining the checklists.

    Staff and managers are portal users with no ORM access to these models,
    so every route checks its group here and reads through sudo().
    """

    # ---------------------------------------------------------------- helpers
    def _user(self):
        user = request.env.user
        return None if (not user or user._is_public()) else user

    def _is_staff(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_STAFF)

    def _is_manager(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_MANAGER)

    def _my_employee(self):
        user = self._user()
        if not user:
            return None
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)

    # ---------------------------------------------------------------- actions
    @http.route('/tasks/done', type='jsonrpc', auth='user')
    def done(self, task_id):
        """Tick a task. A manager may tick anything; staff may tick their own
        work and unassigned items — an opening checklist belongs to whoever
        opens, so "unassigned" means "anyone's to take"."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        task = request.env['modryn.task'].sudo().browse(int(task_id)).exists()
        if not task:
            return {'error': 'not_found'}
        me = self._my_employee()
        if not self._is_manager():
            if not me or (task.employee_id and task.employee_id != me):
                return {'error': 'forbidden'}
        task.action_done(me)
        return {'ok': True, 'task': task._row()}

    @http.route('/tasks/reopen', type='jsonrpc', auth='user')
    def reopen(self, task_id):
        """Manager-only: reopening replaces a completion record, and that is a
        judgement call about somebody else's work."""
        if not self._is_manager():
            return {'error': 'forbidden'}
        task = request.env['modryn.task'].sudo().browse(int(task_id)).exists()
        if not task:
            return {'error': 'not_found'}
        task.action_reopen()
        return {'ok': True, 'task': task._row()}

    # ---------------------------------------------------- owner: checklists
    @http.route('/manage/checklists', type='http', auth='user', website=True, sitemap=False)
    def checklists(self, error=None, **kw):
        if not access.can_view('checklists'):
            return request.not_found()
        templates = request.env['modryn.task.template'].sudo().with_context(
            active_test=False).search([])
        return request.render('modryn_ops.manage_checklists', {
            'templates': templates,
            'error': error,
            'active_tab': 'checklists',
        })

    @http.route('/manage/checklists/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def checklists_new(self, **post):
        if not access.can_view('checklists'):
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/checklists?error=%s' % _("Please enter a name"))
        Template = request.env['modryn.task.template'].sudo()
        if Template.with_context(active_test=False).search_count([('name', '=ilike', name)]):
            return request.redirect(
                '/manage/checklists?error=%s' % _("That checklist item already exists"))
        kind = post.get('kind') if post.get('kind') in ('opening', 'closing') else 'opening'
        # The form posts "11:30"; the model stores wall-clock hours as a float.
        raw = (post.get('due_time') or '').strip()
        try:
            hour, minute = (raw.split(':') + ['0'])[:2]
            due_hour = int(hour) + int(minute) / 60.0
        except (TypeError, ValueError):
            due_hour = 11.0 if kind == 'opening' else 20.0
        if not 0 <= due_hour < 24:
            due_hour = 11.0 if kind == 'opening' else 20.0
        Template.create({'name': name, 'kind': kind, 'due_hour': due_hour})
        return request.redirect('/manage/checklists')

    @http.route('/manage/checklists/archive/<int:template_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True, sitemap=False)
    def checklists_archive(self, template_id, **post):
        if not access.can_view('checklists'):
            return request.not_found()
        template = request.env['modryn.task.template'].sudo().with_context(
            active_test=False).browse(template_id).exists()
        if template:
            # Archive, never delete: past daily instances still reference it.
            template.active = not template.active
        return request.redirect('/manage/checklists')
