from odoo import _, http
from odoo.http import request

from ..models.alteration_task import OPEN_STATES, STATES

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'


class ModrynAtelier(http.Controller):
    """The workshop.

    Managers and owners see everything; a seamstress sees only her own queue and
    advances her own work. Both are portal users with no ORM access to these
    models, so every route checks its group here and reads through sudo().
    """

    # ---------------------------------------------------------------- helpers
    def _user(self):
        user = request.env.user
        return None if (not user or user._is_public()) else user

    def _is_manager(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_MANAGER)

    def _is_staff(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_STAFF)

    def _my_employee(self):
        """The hr.employee behind the signed-in portal user, if any."""
        user = self._user()
        if not user:
            return None
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)

    def _board(self):
        Task = request.env['modryn.alteration.task'].sudo()
        by_state = {}
        for key, label in STATES:
            by_state[key] = [t._row() for t in Task.search([('state', '=', key)])]

        # Load per seamstress counts only OPEN work — delivered gowns are not a
        # burden on anybody.
        load = []
        for employee in request.env['hr.employee'].sudo().search(
                [('modryn_level', 'in', ['manager', 'staff'])]):
            open_tasks = Task.search([
                ('seamstress_id', '=', employee.id), ('state', 'in', OPEN_STATES)])
            if not open_tasks and not employee.modryn_role_id:
                continue
            load.append({
                'id': employee.id,
                'name': employee.name,
                'role': employee.modryn_role_id.name or '',
                'open': len(open_tasks),
                'overdue': len([t for t in open_tasks if t.is_overdue]),
            })
        load.sort(key=lambda r: (-r['open'], r['name']))
        return {'by_state': by_state, 'states': STATES, 'load': load}

    # ------------------------------------------------------------- dashboard
    @http.route('/atelier', type='http', auth='user', website=True, sitemap=False)
    def dashboard(self, **kw):
        if not self._is_manager():
            return request.not_found()
        board = self._board()
        return request.render('modryn_atelier.dashboard', {
            'board': board,
            'seamstresses': request.env['hr.employee'].sudo().search(
                [('modryn_level', 'in', ['manager', 'staff'])]),
            'pieces': request.env['modryn.garment.piece'].sudo().search([]),
        })

    @http.route('/atelier/advance', type='jsonrpc', auth='user')
    def advance(self, task_id, target):
        """Move a task forward.

        A manager may advance anything; a seamstress may advance only her own —
        checked here, server-side, because a task id in a payload is not
        authorisation.
        """
        if not self._is_staff():
            return {'error': 'forbidden'}
        task = request.env['modryn.alteration.task'].sudo().browse(int(task_id)).exists()
        if not task:
            return {'error': 'not_found'}
        if not self._is_manager():
            mine = self._my_employee()
            if not mine or task.seamstress_id != mine:
                return {'error': 'forbidden'}
        if not task.action_advance(target):
            return {'error': 'invalid_transition'}
        return {'ok': True, 'task': task._row()}

    @http.route('/atelier/assign', type='jsonrpc', auth='user')
    def assign(self, task_id, seamstress_id):
        if not self._is_manager():
            return {'error': 'forbidden'}
        task = request.env['modryn.alteration.task'].sudo().browse(int(task_id)).exists()
        employee = request.env['hr.employee'].sudo().browse(int(seamstress_id)).exists()
        if not task or not employee:
            return {'error': 'not_found'}
        task.seamstress_id = employee
        return {'ok': True, 'task': task._row()}

    @http.route('/atelier/my', type='jsonrpc', auth='user')
    def my_tasks(self):
        """A seamstress's own open queue — rendered inside /floor."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        mine = self._my_employee()
        if not mine:
            return {'tasks': []}
        tasks = request.env['modryn.alteration.task'].sudo().search([
            ('seamstress_id', '=', mine.id), ('state', 'in', OPEN_STATES)])
        return {'tasks': [t._row() for t in tasks]}

    # ------------------------------------------------------- garment pieces
    @http.route('/manage/pieces', type='http', auth='user', website=True, sitemap=False)
    def pieces(self, error=None, **kw):
        if not self._user() or not request.env.user.has_group(GROUP_OWNER):
            return request.not_found()
        return request.render('modryn_atelier.manage_pieces', {
            'pieces': request.env['modryn.garment.piece'].sudo().with_context(
                active_test=False).search([]),
            'error': error,
            'active_tab': 'pieces',
        })

    @http.route('/manage/pieces/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def pieces_new(self, **post):
        if not self._user() or not request.env.user.has_group(GROUP_OWNER):
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/pieces?error=%s' % _("Please enter a name"))
        Piece = request.env['modryn.garment.piece'].sudo()
        if Piece.with_context(active_test=False).search_count([('name', '=ilike', name)]):
            return request.redirect(
                '/manage/pieces?error=%s' % _("That garment piece already exists"))
        Piece.create({'name': name})
        return request.redirect('/manage/pieces')

    @http.route('/manage/pieces/archive/<int:piece_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def pieces_archive(self, piece_id, **post):
        if not self._user() or not request.env.user.has_group(GROUP_OWNER):
            return request.not_found()
        piece = request.env['modryn.garment.piece'].sudo().with_context(
            active_test=False).browse(piece_id).exists()
        if piece:
            # Archive, never delete: existing tasks still reference this piece.
            piece.active = not piece.active
        return request.redirect('/manage/pieces')
