from datetime import datetime

import werkzeug.urls

from odoo import _, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access
from odoo.addons.modryn_staff.controllers.floor import ModrynFloor
from odoo.addons.modryn_staff.controllers.home import ModrynHome

from ..models.alteration_task import OPEN_STATES, PRIORITIES, STATES

_lt = LazyTranslate(__name__)

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'

nav.register('atelier', '/atelier', _lt("Workshop"), 30, icon='fa-scissors')
# NOT in the navbar. The garment pieces are a sub-page of the workshop and the
# workshop links to them ("Garment pieces", top of /atelier) - a row of their own
# was a second door to a screen most people open once a year, in a bottom row
# that had grown into a rank of them.
#
# Which is why the routes below ask can_view('ATELIER') and not can_view('pieces'):
# with nothing registered, 'pieces' is a key the matrix has never heard of and
# can_view would refuse everybody but the owner. Whoever may open the workshop
# may maintain the pieces it works on.


def my_open_task_rows():
    """The signed-in employee's own open queue.

    ONE source for its three readers — /atelier/my, the floor board panel and
    the staff home page — so the views cannot drift apart. Module-level
    because the readers live on three different controller classes.
    """
    user = request.env.user
    if not user or user._is_public():
        return []
    mine = request.env['hr.employee'].sudo().search(
        [('user_id', '=', user.id)], limit=1)
    if not mine:
        return []
    tasks = request.env['modryn.alteration.task'].sudo().search([
        ('seamstress_id', '=', mine.id), ('state', 'in', OPEN_STATES)])
    return [t._row() for t in tasks]


class ModrynFloorAtelier(ModrynFloor):
    """Controller inheritance: the floor board learns about the workshop only
    when this module is installed. modryn_staff itself stays atelier-ignorant,
    so it keeps working in a database without alterations."""

    # NO `only` here, and that is the whole point of the mistake this line is
    # correcting. This _board is the FLOOR's - a different board with a
    # different superclass, which takes no filter. Threading the workshop's
    # filter through it made /floor/data raise TypeError, and the gate said so:
    # "no result - server error?" plus "board pending panel - key missing".
    def _board(self):
        board = super()._board()

        # The signed-in employee's own open work — the seamstress panel.
        board['my_tasks'] = my_open_task_rows()

        # What the finish modal needs to build a task without extra round-trips.
        board['atelier'] = {
            'pieces': [{'id': p.id, 'name': p.name}
                       for p in request.env['modryn.garment.piece'].sudo().search([])],
        }
        return board


class ModrynHomeAtelier(ModrynHome):
    """Her alterations, on her own page — the same rows the floor panel shows."""

    def _home(self):
        home = super()._home()
        home['my_tasks'] = my_open_task_rows()
        # The three working states, so her board can offer ALL of them instead of
        # only the next one — that is what makes a mis-tap correctable.
        # Built here, in the module that owns the model: modryn_atelier depends
        # on modryn_staff and never the other way round, so modryn_staff must not
        # import this list.
        # 'delivered' is excluded on purpose. It stamps delivered_at and releases
        # her to the next job; it is a one-way door and does not belong in a row
        # of toggles.
        # Off the FIELD, not off the constant: these three words are buttons on
        # her main screen and the constant is plain English Python. Odoo
        # translates a Selection's labels; it cannot translate a list nobody
        # asked it about.
        labels = dict(request.env['modryn.alteration.task'].sudo()
                      .modryn_selection('state'))
        home['task_states'] = [(code, labels.get(code, code))
                               for code in OPEN_STATES]
        return home


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

    def _board(self, only=None):
        """The whole workshop, or one seamstress's part of it.

        `only` narrows the four panels and the queue and leaves the workload
        column alone: that column is how you got here and how you get back out,
        and a filter that hides the control which applied it is a trap.
        """
        Task = request.env['modryn.alteration.task'].sudo()
        mine = [('seamstress_id', '=', only.id)] if only else []
        by_state = {}
        for key, label in STATES:
            by_state[key] = [t._row()
                             for t in Task.search([('state', '=', key)] + mine)]

        # The queue: open work nobody holds yet, in the exact order the
        # auto-assigner will hand it out (priority, then the clock) — so what
        # the manager sees IS what the next free seamstress gets.
        # Held by nobody - so when the board is filtered to one woman, the
        # queue is empty by definition rather than showing work that is not
        # hers.
        queue = [] if only else [t._row() for t in Task.search([
            ('seamstress_id', '=', False), ('state', 'in', OPEN_STATES)])]

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
        # Off the FIELD, not off the constant - the same reason spelled out on
        # the home screen's task_states above. These four words are the board's
        # column headings and the labels on every move button; read from STATES
        # they were English on a Hebrew page and on an Arabic one, which is
        # every reader this product has.
        labels = dict(Task.modryn_selection('state'))
        states = [(code, labels.get(code, label)) for code, label in STATES]
        return {'by_state': by_state, 'states': states, 'load': load,
                'queue': queue, 'only': only}

    def _variant_rows(self):
        """The rail, flattened for the search box.

        Serial and kind come from MODRYN_OPS, which this module does not depend
        on - the workshop is meant to work in a boutique that has not installed
        the catalogue. Guarded with `in _fields` rather than assumed, the same
        way booking guards modryn_cancelled_at: a shop without the catalogue
        searches by name alone and nothing raises.
        """
        Template = request.env['product.template']
        has_serial = 'modryn_serial' in Template._fields
        has_kind = 'modryn_type_id' in Template._fields
        rows = []
        for variant in request.env['product.product'].sudo().search(
                [('product_tmpl_id.is_published', '=', True)]):
            tmpl = variant.product_tmpl_id
            size = variant.product_template_attribute_value_ids[:1].name
            rows.append({
                'id': variant.id,
                'name': tmpl.name or '',
                'label': '%s%s' % (tmpl.name or '', ' · %s' % size if size else ''),
                'serial': (tmpl.modryn_serial or '') if has_serial else '',
                'kind': (tmpl.modryn_type_id.name or '') if has_kind else '',
            })
        return rows

    # ------------------------------------------------------------- dashboard
    @http.route('/atelier', type='http', auth='user', website=True, sitemap=False)
    def dashboard(self, error=None, **kw):
        # Matrix-gated: managers and owner always pass; a staff role reaches
        # this only when the owner ticks Workshop for it — which is exactly how
        # a seamstress gets her dashboard without becoming a manager.
        if not access.can_view('atelier'):
            return access.deny()
        # Whose board. A name that is not a number, or one that is nobody, falls
        # back to the whole workshop rather than to an empty page - a stale link
        # should land somewhere real.
        only = None
        if str(kw.get('who', '')).isdigit():
            only = request.env['hr.employee'].sudo().search([
                ('id', '=', int(kw['who'])),
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
            ], limit=1) or None
        board = self._board(only=only)
        return request.render('modryn_atelier.dashboard', {
            'board': board,
            'is_manager': self._is_manager(),
            'error': error,
            'seamstresses': request.env['hr.employee'].sudo().search(
                [('modryn_level', 'in', ['manager', 'staff'])]),
            'pieces': request.env['modryn.garment.piece'].sudo().search([]),
            'variants': self._variant_rows(),
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

    def _may_touch(self, task):
        """A manager touches anything; a seamstress touches her own.

        The same rule /atelier/advance applies, in one place both can call.
        Applied on the ROUTE and not in the markup: hiding a button is not a
        permission, and a task id in a form is not authorisation.
        """
        if self._is_manager():
            return True
        mine = self._my_employee()
        return bool(mine) and task.seamstress_id == mine

    @staticmethod
    def _back_to(task, error=None):
        """Back to the ROW she pressed on, not the top of the page.

        Every control here is a plain form, so the browser reloads - and a
        reload lands at the top of a board that runs several screens long, with
        the row she acted on nowhere in sight. That is what a press that
        "did nothing" actually was.
        """
        url = '/atelier'
        if error:
            url += '?error=%s' % werkzeug.urls.url_quote(error)
        return request.redirect('%s#task-%s' % (url, task.id))

    @http.route('/atelier/task/move', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def task_move(self, **post):
        """Take it, put it back, mark it ready, hand it over.

        One route for every move rather than a Take button and a separate undo:
        moving between the open states is free in both directions in the model
        already, and a mis-tap is repaired by pressing the state she meant.
        """
        if not self._is_staff():
            return access.deny()
        task = request.env['modryn.alteration.task'].sudo().browse(
            int(post['task_id']) if str(post.get('task_id', '')).isdigit() else 0
        ).exists()
        if not task:
            return request.redirect('/atelier')
        if not self._may_touch(task):
            return self._back_to(task, _("That job belongs to somebody else."))
        target = post.get('target')

        # BACK TO THE QUEUE, which is not a state - it is a state and an owner.
        # תור העבודות is the work nobody holds, so letting go of a dress means
        # clearing the name as well as the column. Undoing "I'm on it" used to
        # take only the first half: the row moved back and her name stayed on
        # it, so it never reappeared in the queue and nobody else could pick it
        # up.
        #
        # Not a fifth STATE, deliberately: the queue is a view of the other four
        # (open, unheld), and making it a state would put every task in two
        # places at once for every reader that groups by state.
        if target == 'queue':
            # Delivered can come back now, so putting one back means undoing the
            # handover as well as letting go of it - action_advance clears
            # delivered_at, which is the half that would otherwise leave a job
            # in the queue carrying a date it was handed over on.
            if task.state == 'delivered' and not task.action_advance('intake'):
                return self._back_to(task, _("That job cannot move there."))
            task.seamstress_id = False
            if task.state != 'intake' and not task.action_advance('intake'):
                return self._back_to(task, _("That job cannot move there."))
            return self._back_to(task)

        # Taking a job that is nobody's makes it HERS. Without this a seamstress
        # presses "I'm on it", the row moves to In progress with Unassigned
        # beside it, and the workload column she is judged by never counts it.
        if (target == 'in_progress' and not task.seamstress_id
                and not self._is_manager()):
            mine = self._my_employee()
            if mine:
                task.seamstress_id = mine.id
        if not task.action_advance(target):
            return self._back_to(task, _("That job cannot move there."))
        return self._back_to(task)

    @http.route('/atelier/task/edit', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def task_edit(self, **post):
        """The due date and the instructions, from the board.

        Both were write-once: set when the task was created and unreachable
        afterwards without the Odoo back office. A date the workshop cannot move
        is a date that stops being true the first time a bride changes her
        fitting.
        """
        if not self._is_staff():
            return access.deny()
        task = request.env['modryn.alteration.task'].sudo().browse(
            int(post['task_id']) if str(post.get('task_id', '')).isdigit() else 0
        ).exists()
        if not task:
            return request.redirect('/atelier')
        if not self._may_touch(task):
            return self._back_to(task, _("That job belongs to somebody else."))

        values = {'note': (post.get('note') or '').strip()}
        raw = (post.get('due_date') or '').strip()
        if raw:
            try:
                values['due_date'] = datetime.strptime(raw, '%Y-%m-%d').date()
            except (TypeError, ValueError):
                return self._back_to(task, _("That date could not be read."))
        else:
            # Cleared on purpose is a real answer: work with no deadline sorts
            # last inside its priority band rather than pretending to be urgent.
            values['due_date'] = False
        task.write(values)
        return self._back_to(task)

    @http.route('/atelier/assign', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def assign(self, **post):
        """Reassign from the dashboard's inline per-row form.

        Was a jsonrpc route that NOTHING ever called — the dashboard rendered
        the seamstress as plain text and the only assignment door was the floor
        finish modal. Converted to a plain form POST rather than wiring JS:
        an empty seamstress puts the task back on the auto-assign queue.
        """
        if not self._is_manager():
            return access.deny()
        task = request.env['modryn.alteration.task'].sudo().browse(
            int(post.get('task_id', 0) or 0)).exists()
        if not task:
            return request.redirect('/atelier')
        seamstress_id = post.get('seamstress_id')
        if seamstress_id:
            employee = request.env['hr.employee'].sudo().browse(
                int(seamstress_id)).exists()
            task.seamstress_id = employee or False
        else:
            task.seamstress_id = False
        return request.redirect('/atelier')

    @http.route('/atelier/my', type='jsonrpc', auth='user')
    def my_tasks(self):
        """A seamstress's own open queue — rendered inside /floor."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        return {'tasks': my_open_task_rows()}

    @http.route('/atelier/task/create', type='jsonrpc', auth='user')
    def task_create(self, customer_name, customer_phone=None, variant_id=None,
                    piece_ids=None, note=None, seamstress_id=None, due_date=None,
                    priority=None):
        """The finish-screen handoff: fitting done, alteration work begins.

        Always lands in 'intake' even when a seamstress is chosen — being
        assigned is not the same as having started, and the workshop dashboard
        reads intake as "on the pile".

        Priority and due date are REQUIRED here, at the single creation door:
        the queue orders by them, and a task without either would sit wherever
        the defaults happened to drop it, invisible to the manager who thought
        she had said how urgent it was.
        """
        # Staff, not manager: a seamstress must be able to write down a garment
        # that arrives in her hands. She may not hand work to SOMEBODY ELSE
        # though — assignment stays a manager's act, enforced below rather than
        # by hiding a dropdown, because a payload is not authorisation.
        if not self._is_staff():
            return {'error': 'forbidden'}
        if not self._is_manager():
            mine = self._my_employee()
            seamstress_id = mine.id if mine else None
        name = (customer_name or '').strip()
        if not name:
            return {'error': 'missing_customer'}
        if priority not in [p[0] for p in PRIORITIES]:
            return {'error': 'missing_priority'}
        if not due_date:
            return {'error': 'missing_due'}

        values = {
            'customer_name': name,
            'customer_phone': (customer_phone or '').strip(),
            'note': (note or '').strip(),
            'state': 'intake',
            'priority': priority,
        }
        if variant_id:
            variant = request.env['product.product'].sudo().browse(int(variant_id)).exists()
            values['variant_id'] = variant.id if variant else False
        if piece_ids:
            pieces = request.env['modryn.garment.piece'].sudo().browse(
                [int(p) for p in piece_ids]).exists()
            values['piece_ids'] = [(6, 0, pieces.ids)]
        if seamstress_id:
            employee = request.env['hr.employee'].sudo().browse(int(seamstress_id)).exists()
            values['seamstress_id'] = employee.id if employee else False
        if due_date:
            values['due_date'] = due_date

        task = request.env['modryn.alteration.task'].sudo().create(values)
        return {'ok': True, 'task': task._row()}

    def _task_error(self, key):
        # Per request, not module level: _() around a LOOKUP would hide the
        # literals from the extractor (.memory/odoo-traps.md #9).
        labels = {
            'missing_customer': _("Please enter the customer's name"),
            'missing_priority': _("Please choose a priority"),
            'missing_due': _("Please choose a due date"),
        }
        return labels.get(key, _("Something went wrong."))

    @http.route('/atelier/task/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def task_new(self, **post):
        """The dashboard's own creation door.

        Until now a task could only be born from the floor finish modal —
        closing a walk-in or a sold booking. A garment that arrives any other
        way (brought in for alterations, phoned in) had no entry point at all.

        Calls task_create() directly: a decorated route method is still a plain
        callable, so the validation and creation stay single-sourced with the
        jsonrpc contract that qa act 6c and the k6 manager scenario pin.
        """
        if not self._is_staff():            # hiding the panel is not a permission
            return access.deny()
        result = self.task_create(
            customer_name=post.get('customer_name'),
            customer_phone=post.get('customer_phone'),
            variant_id=post.get('variant_id') or None,
            piece_ids=request.httprequest.form.getlist('piece_ids'),
            note=post.get('note'),
            seamstress_id=post.get('seamstress_id') or None,
            due_date=post.get('due_date') or None,
            priority=post.get('priority'))
        if result.get('error'):
            return request.redirect(
                '/atelier?error=%s' % self._task_error(result['error']))
        return request.redirect('/atelier')

    # ------------------------------------------------------- garment pieces
    @http.route('/manage/pieces', type='http', auth='user', website=True, sitemap=False)
    def pieces(self, error=None, **kw):
        if not access.can_view('atelier'):
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
        if not access.can_view('atelier'):
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
        if not access.can_view('atelier'):
            return request.not_found()
        piece = request.env['modryn.garment.piece'].sudo().with_context(
            active_test=False).browse(piece_id).exists()
        if piece:
            # Archive, never delete: existing tasks still reference this piece.
            piece.active = not piece.active
        return request.redirect('/manage/pieces')
