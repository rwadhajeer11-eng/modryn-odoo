"""The manager's own screen.

A hub rather than a page about one thing: the first block on it is the one the
owner asked for - say something to the team - and the rest of what a manager
does all day already has screens, so this is where the things that do not fit
one of those will land.

Sending is MANAGER-level, not merely page-level. The role matrix decides what a
role can SEE and levels decide what it can DO, which is the rule every other
controller here follows; an announcement reaches every phone in the shop, and
that is not a thing a page grant should be able to hand out.
"""

from datetime import datetime

import pytz
import werkzeug.urls

from odoo import _, http
from odoo.http import content_disposition, request
from odoo.tools.translate import LazyTranslate

from .. import nav
from . import access
from .manage import ModrynManage, _file_papers, _levels, _personal_from

_lt = LazyTranslate(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# How many past announcements the screen shows. Enough to see what the team was
# told this week and to unsend the one that was wrong; a manager who wants the
# whole history of a boutique is asking a different question than this screen.
RECENT = 20


class ModrynManagerScreen(http.Controller):

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _team(self, archived=False):
        """Everyone who could be told something, in a stable order.

        active_test=False and an explicit `active` term, rather than letting the
        default do it: the announcement picker and the team box read this, and
        a silent default is how the archived list came to be unreachable in the
        first place.
        """
        return request.env['hr.employee'].sudo().with_context(
            active_test=False).search([
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
                ('active', '=', not archived),
            ], order='name')

    def _papers(self, employee):
        """What the boutique holds for one woman.

        The same query her own profile runs, against the same attachments: this
        screen and that one are two doors onto one folder, not two folders that
        have to be kept in step.
        """
        return request.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.employee'),
            ('res_id', '=', employee.id),
            ('name', '!=', False),
        ], order='create_date desc')

    def _team_rows(self, archived=False):
        """Everything the Team screen shows, plus the papers.

        Built from that screen's OWN row builder rather than a second query
        assembling the same fields: one function and two readers cannot start
        disagreeing about what a woman's phone number is, and a copy would have
        quietly missed the next field somebody adds.
        """
        rows = ModrynManage()._employee_rows()
        by_id = {employee.id: employee for employee in self._team(archived)}
        out = []
        for row in rows:
            employee = by_id.get(row['id'])
            if not employee:
                # Not in the half being asked for. Her papers do not vanish
                # either way - they stay against her record and come back with
                # her if she is restored.
                continue
            out.append(dict(row, papers=[{
                'id': doc.id,
                'name': doc.name,
                'at': doc.create_date.strftime('%d.%m.%Y')
                      if doc.create_date else '',
            } for doc in self._papers(employee)]))
        return out

    def _recent(self):
        Announcement = request.env['modryn.announcement'].sudo()
        rows = []
        for record in Announcement.search([], limit=RECENT):
            people = record.employee_ids
            rows.append({
                'id': record.id,
                'body': record.body or '',
                'author': record.author_name or '',
                # Whom it went to, as names rather than a count: "everyone" and
                # "these four" are different facts and a manager unsending
                # something needs to see which one she sent.
                'to': ', '.join(people.mapped('name')),
                'everyone': not people,
                'at': pytz.utc.localize(record.create_date).astimezone(TZ)
                          .strftime('%d.%m %H:%M') if record.create_date else '',
            })
        return rows

    # The blocks this screen is made of, in the order they are offered. A list
    # rather than a template full of hard-coded tiles: adding the next one is a
    # line here, and the tiles and the routing cannot disagree about what
    # exists.
    # 'hours' is the boutique's OPENING hours; 'worked' is who stood in it.
    # Two different questions that both want the word - named apart here so a
    # link can never quietly open the other one.
    VIEWS = ('announce', 'team', 'hours', 'rooms', 'worked', 'sales')

    def _account_form(self, editing=None, adding=False, errors=None, values=None):
        """The add/edit form's context, or nothing at all.

        Returned empty unless the box is actually showing the form, so a manager
        reading the cards never has a roles list and a levels list built for a
        form she is not going to be shown.
        """
        if not (adding or editing):
            return {}
        employee = None
        if editing:
            employee = request.env['hr.employee'].sudo().with_context(
                active_test=False).browse(editing).exists()
            if not employee:
                return {}
        if values is None:
            # Her stored answers, or an empty form. The same shape
            # staff_edit_form built, so the lifted markup reads it unchanged.
            values = dict(
                _personal_from(employee),
                name=employee.name,
                phone=employee.work_phone or '',
                role_ids=employee.modryn_role_ids.ids,
                level=employee.modryn_level,
            ) if employee else {}
        return {
            'form_open': True,
            'roles': ModrynManage()._roles(),
            'levels': _levels(),
            'employee': employee,
            'errors': errors or {},
            'values': values,
            # Only when she EXISTS. A woman being added has no folder yet and
            # nowhere to hang one: the file field on the add form travels with
            # the same Save that creates her.
            'form_papers': [{
                'id': doc.id,
                'name': doc.name,
                'at': doc.create_date.strftime('%d.%m.%Y'),
            } for doc in self._papers(employee)] if employee else [],
        }

    def _worked_context(self, whose=None, month=None, error=None):
        """The team to choose from, and one woman's month if one is chosen.

        The month itself comes from ModrynStaffAuth._hours_context - the same
        function her own profile calls, against the same model method. A second
        implementation here would be a second answer to "how many hours in
        March", and the two would drift on the first change to either.
        """
        from .auth import ModrynStaffAuth

        def row(e):
            return {'id': e.id, 'name': e.name,
                    'chosen': bool(whose) and e.id == whose.id}

        context = {
            'worked_team': [row(e) for e in self._team()],
            # The women who have LEFT, offered separately. Their hours are what
            # a last pay packet is worked out from, so leaving them out of this
            # picker makes the one month somebody actually needs the only one
            # they cannot reach. Grouped rather than mixed in, because "who is
            # on this month" is the question asked nine times in ten.
            'worked_gone': [row(e) for e in self._team(archived=True)],
            'worked_who': whose,
            'worked_error': error,
        }
        if whose:
            hours = ModrynStaffAuth()._hours_context(whose, month)
            context.update(hours)
            # The month actually being shown, which is not always the month
            # asked for: _hours_context falls back to her most recent one when
            # the request names a month she has nothing in. Computed here, once,
            # rather than by a list comprehension inside three t-att-values -
            # a form that posts back a DIFFERENT month than the page is showing
            # sends the manager somewhere she did not come from.
            context['chosen_month'] = next(
                (m['key'] for m in hours['months'] if m['chosen']), '')
        return context

    # Two characters, the same floor the dress picker and the customer lookup
    # use. One letter matches most of the shop and answers nothing.
    SALES_MIN_QUERY = 2

    def _sales(self, query):
        """Everything sold to whoever matches `query`, newest first.

        Read live rather than kept in a summary table: a sale is written once
        and never changes, so there is nothing to keep in step, and a stored
        rollup would be a staleness bug waiting for a backfill. The same
        reasoning the reports page already applies to its own figures.
        """
        query = (query or '').strip()
        if len(query) < self.SALES_MIN_QUERY:
            return []
        like = '%%%s%%' % query
        rows = []

        # The appointments. modryn_outcome lives in modryn_ops, so a boutique
        # without the catalogue module has no sales history rather than a
        # traceback - the same `in _fields` guard booking uses for cancellations.
        Event = request.env['calendar.event']
        if 'modryn_outcome' in Event._fields:
            for event in Event.sudo().search([
                    ('modryn_is_booking', '=', True),
                    ('modryn_outcome', '=', 'sold'),
                    '|', ('name', 'ilike', like),
                         ('modryn_customer_phone', 'ilike', like),
            ], order='modryn_outcome_at desc', limit=60):
                rows.append({
                    'kind': 'booking',
                    'id': event.id,
                    'name': event.name or '',
                    'phone': event.modryn_customer_phone or '',
                    'dress': event.modryn_sale_items or '',
                    'amount': round(event.modryn_sale_amount or 0.0),
                    # What she actually paid, typed by the stylist who closed it.
                    'amount_is_catalogue': False,
                    'when': event.modryn_outcome_at,
                    'by': event.modryn_outcome_by_id.name or '',
                })

        # The walk-ins. No typed price on this side - the floor records WHICH
        # dress left, so the catalogue price is the best available answer and is
        # labelled as such rather than passed off as the till.
        Entry = request.env['modryn.queue.entry']
        if 'modryn_outcome' in Entry._fields:
            for entry in Entry.sudo().search([
                    ('modryn_outcome', '=', 'sold'),
                    '|', ('name', 'ilike', like), ('phone', 'ilike', like),
            ], order='modryn_outcome_at desc', limit=60):
                variant = entry.modryn_variant_id
                size = variant.product_template_attribute_value_ids[:1].name
                rows.append({
                    'kind': 'walkin',
                    'id': entry.id,
                    'name': entry.name or '',
                    'phone': entry.phone or '',
                    'dress': '%s%s' % (
                        variant.product_tmpl_id.name or '',
                        ' · %s' % size if size else '') if variant else '',
                    'amount': round(variant.list_price or 0.0) if variant else 0,
                    'amount_is_catalogue': True,
                    'when': entry.modryn_outcome_at,
                    'by': entry.modryn_employee_id.name or '',
                })

        # Newest first across BOTH kinds, which a single query could not do.
        # A sale with no recorded moment sorts last rather than crashing the
        # comparison: those are old rows from before the timestamp existed.
        rows.sort(key=lambda r: (r['when'] is not None, r['when']), reverse=True)

        # And what the workshop did to it. Matched on PHONE - a name is spelled
        # three ways by three people and a number is a number - so a bride who
        # bought and then had it taken in reads as one story on one row.
        Task = request.env['modryn.alteration.task'].sudo()
        for row in rows:
            row['alterations'] = [{
                'what': task.note or '',
                'pieces': ', '.join(task.piece_ids.mapped('name')),
                'taken_in': task.create_date,
                'finished': task.delivered_at,
                'state': dict(task._fields['state'].selection).get(
                    task.state, task.state),
                'done': task.state == 'delivered',
            } for task in Task.search(
                self._same_person(row), order='create_date desc')]
        return rows

    @staticmethod
    def _same_person(row):
        """Her alteration work, by number OR by name.

        Phone first, because it is this product's contact identity - but not
        phone ONLY. The workshop takes a garment in at the counter and the
        number typed there is whoever answered: a mother's, a second phone, or
        nothing at all. Matching on phone alone lost every one of those, and the
        bride reads as somebody who bought a dress and never had it touched.

        The cost of the wider match is two brides who share a name being shown
        each other's alterations. Against losing the connection entirely, on a
        screen the owner reads rather than one that acts on the answer, that is
        the better way to be wrong - and the number is on the card, so she can
        see which is which.
        """
        terms = []
        if row.get('phone'):
            terms.append(('customer_phone', '=', row['phone']))
        if row.get('name'):
            terms.append(('customer_name', '=', row['name']))
        if not terms:
            # Never an empty domain: that would hand back every alteration the
            # boutique has ever done and hang it on one bride.
            return [('id', '=', 0)]
        return (['|'] * (len(terms) - 1)) + terms

    def _render(self, error=None, draft=None, picked=None, file_error=None,
                file_for=None, view=None, editing=None, adding=False,
                form_errors=None, form_values=None, archived=False, whose=None):
        context = {
            # None means the tiles themselves. Anything unrecognised falls back
            # to them rather than 404ing: a stale link should land somewhere
            # useful, not on an error.
            'view': view if view in self.VIEWS else None,
            'team': [{'id': e.id, 'name': e.name} for e in self._team()],
            # One half or the other. The announcement picker above stays on
            # the ACTIVE list whatever this says: telling a woman who left in
            # March about Thursday's delivery is not a thing to offer.
            'team_rows': self._team_rows(archived),
            'showing_archived': archived,
            # Adding a person, setting a password, changing a level: the owner's
            # alone. The cards carry the links for her so she loses nothing by
            # the move, and a manager simply does not see them.
            'is_owner': access.is_owner(),
            'announcements': self._recent(),
            'error': error,
            # Which worker's upload went wrong, so the sentence appears against
            # HER card rather than at the top of a screen listing eight people.
            'file_error': file_error,
            'file_for': file_for,
            # What she typed, handed back so a refusal never costs her the
            # message she had written.
            'draft': draft or '',
            'picked': picked or [],
            'active_tab': 'boss',
        }
        # The owner's panels, and only when she is going to see them: building a
        # closures list, or a roles list, for a manager who will never be shown
        # either is work for nobody.
        if view == 'sales':
            # The whole history, searched. Nothing is drawn until she has typed
            # two characters: a page that opens on every sale the boutique has
            # ever made is a page nobody can read, and the question here is
            # always about one bride.
            q = request.params.get('q') or ''
            context['sales_query'] = q
            context['sales'] = self._sales(q)
            context['sales_min'] = self.SALES_MIN_QUERY
        if view == 'worked':
            # The MANAGER's, deliberately, unlike the four panels below. She is
            # the one standing there when somebody goes home without pressing
            # the button; making her find the owner to correct it is how the
            # figure stays wrong.
            context.update(self._worked_context(
                whose=whose, month=request.params.get('month'),
                error=request.params.get('error')))
        if access.is_owner():
            if view == 'hours':
                context.update(ModrynManage().hours_context(
                    error=request.params.get('error')))
            elif view == 'rooms':
                context.update(ModrynManage().rooms_context(
                    error=request.params.get('error')))
            elif view == 'team':
                context.update(self._account_form(
                    editing=editing, adding=adding,
                    errors=form_errors, values=form_values))
        return request.render('modryn_staff.manager_screen', context)

    @http.route('/manage/team-screen', type='http', auth='user', website=True,
                sitemap=False)
    def manager_screen(self, **kw):
        # A MANAGER always, plus whoever the owner grants it to. The screen is
        # named after her: moving it into the bottom row put it behind a grant
        # like everything else down there, and a manager arriving at her own
        # screen to find nothing on it is not a permission decision anybody
        # made. A named exception rather than loosening the whole row - the rest
        # of it is the boutique's administration and stays granted.
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        # The form is a sub-state of the team tile, not a view of its own: a
        # stale ?view=team&edit=<gone> falls back to the cards rather than to an
        # error, because _account_form returns nothing for a person who is not
        # there any more.
        try:
            editing = int(kw['edit']) if kw.get('edit') else None
        except (TypeError, ValueError):
            editing = None
        # Whose hours. Read here rather than in the context builder so an id
        # for somebody who has since left falls back to the picker instead of a
        # traceback - the same fallback the team form's stale ?edit= gets.
        whose = None
        if kw.get('whose'):
            try:
                whose = request.env['hr.employee'].sudo().browse(
                    int(kw['whose'])).exists()
            except (TypeError, ValueError):
                whose = None
        return self._render(view=kw.get('view'), editing=editing,
                            adding=bool(kw.get('new')),
                            archived=bool(kw.get('archived')),
                            whose=whose or None)

    @http.route('/manage/team-screen/announce', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def announce(self, **post):
        if not access.is_manager():
            return access.deny()
        body = (post.get('body') or '').strip()
        # getlist, and replace-set semantics: the same idiom manage.py uses for
        # its page matrix. An empty selection is the meaningful "everyone",
        # not a missing value.
        raw = request.httprequest.form.getlist('employee_ids')
        ids = []
        for value in raw:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        employees = request.env['hr.employee'].sudo().browse(ids).exists()
        if not body:
            return self._render(
                error='empty', draft=body, picked=employees.ids, view='announce')
        record = request.env['modryn.announcement'].sudo().modryn_publish(
            body, employees, self._my_employee())
        if not record:
            # The only way to get here with a body: every person ticked is the
            # author herself. Said out loud rather than redirecting to a list
            # that would show nothing new and leave her wondering.
            return self._render(
                error='nobody', draft=body, picked=employees.ids, view='announce')
        return request.redirect('/manage/team-screen?view=announce')

    # ------------------------------------------------------ her papers
    def _team_member(self, raw_id):
        """One of the boutique's own people, by id from a form. Or nothing.

        Two guards in one place because both are about the same lie: a form
        field is whatever arrives, not what the template put there. int() on a
        non-number is a 500 rather than a refusal, and browsing an id that is
        not staff files a shift against somebody no screen here can show.
        """
        try:
            employee_id = int(raw_id or 0)
        except (TypeError, ValueError):
            return request.env['hr.employee']
        if employee_id <= 0:
            return request.env['hr.employee']
        return request.env['hr.employee'].sudo().with_context(
            active_test=False).search([
                ('id', '=', employee_id),
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
            ], limit=1)

    # ------------------------------------------------- who was on the floor
    def _worked_back(self, employee_id, month=None, error=None):
        """Back to the month she was just looking at."""
        target = '/manage/team-screen?view=worked&whose=%d' % employee_id
        if month:
            target += '&month=%s' % werkzeug.urls.url_quote(month)
        if error:
            target += '&error=%s' % error
        return request.redirect(target)

    @http.route('/manage/team-screen/worked/amend', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def worked_amend(self, **post):
        """Set what a spell really was, or clear it away.

        One route for both because they are one decision on the page - the row
        has a Save and a Remove, and splitting them into two addresses is two
        places to get the permission check right.
        """
        if not access.is_manager():
            return access.deny()
        Attendance = request.env['modryn.shift.attendance']
        try:
            spell = Attendance.sudo().browse(int(post.get('spell_id') or 0)).exists()
        except (TypeError, ValueError):
            spell = None
        if not spell:
            return request.redirect('/manage/team-screen?view=worked')
        employee_id = spell.employee_id.id
        month = post.get('month')

        if post.get('drop'):
            # A mis-tap, deleted rather than zeroed. A spell corrected to no
            # length would still be a line on her month claiming she came in.
            spell.unlink()
            return self._worked_back(employee_id, month)

        day = (post.get('day') or '').strip()
        try:
            day = datetime.strptime(day, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return self._worked_back(employee_id, month, 'badtime')
        error = spell.modryn_amend(day, post.get('came'), post.get('went'))
        return self._worked_back(employee_id, month, error)

    @http.route('/manage/team-screen/worked/add', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def worked_add(self, **post):
        """A shift nobody pressed the button for at either end."""
        if not access.is_manager():
            return access.deny()
        employee = self._team_member(post.get('employee_id'))
        if not employee:
            return request.redirect('/manage/team-screen?view=worked')
        month = post.get('month')
        try:
            day = datetime.strptime(
                (post.get('day') or '').strip(), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return self._worked_back(employee.id, month, 'badtime')
        error = request.env['modryn.shift.attendance'].modryn_add_spell(
            employee, day, post.get('came'), post.get('went'))
        # Land on the month she just wrote INTO, not the one she was reading.
        # Adding a February shift while looking at March and being shown March
        # unchanged reads exactly like the save having failed.
        return self._worked_back(
            employee.id, month if error else day.strftime('%Y-%m'), error)

    @http.route('/manage/team-screen/paper', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def paper_upload(self, **post):
        """File a signed page against one woman's record.

        Still here for the form's own file field to post to. Owner-only, the
        same as the form that draws it.
        """
        if not access.is_owner():
            return access.deny()
        employee = self._team_member(post.get('employee_id'))
        if not employee:
            return request.redirect('/manage/team-screen?view=team')

        uploads = [u for u in request.httprequest.files.getlist('paper')
                   if u and u.filename]
        if not uploads:
            return self._render(view='team', editing=employee.id,
                                form_errors={'paper': _("Choose a file first.")})
        if _file_papers(employee, uploads):
            return self._render(
                view='team', editing=employee.id,
                form_errors={'paper': _("That file is too large — "
                                        "the limit is 10 MB.")})
        return request.redirect(
            '/manage/team-screen?view=team&edit=%d' % employee.id)

    @http.route('/manage/team-screen/paper/<int:attachment_id>', type='http',
                auth='user', website=True, sitemap=False)
    def paper_file(self, attachment_id, **kw):
        """Read one back. Through this route and never /web/content, which
        serves any attachment its ACL allows - and ir.attachment's rules are
        about models, not about which woman owns which payslip."""
        if not access.is_manager():
            return access.deny()
        attachment = request.env['ir.attachment'].sudo().browse(
            attachment_id).exists()
        # It must be a STAFF paper. A manager may read her whole team's folder,
        # but that is not permission to read every attachment in the database
        # by guessing an id.
        if not attachment or attachment.res_model != 'hr.employee':
            return request.not_found()
        return request.make_response(attachment.raw, headers=[
            # Downloaded, never rendered: an uploaded .html or .svg served
            # inline runs in the boutique's own origin, which is stored XSS
            # with extra steps.
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', content_disposition(attachment.name)),
            ('X-Content-Type-Options', 'nosniff'),
        ])

    @http.route('/manage/team-screen/paper/<int:attachment_id>/remove',
                type='http', auth='user', website=True, methods=['POST'],
                csrf=True, sitemap=False)
    def paper_remove(self, attachment_id, **post):
        # The OWNER's. This used to read is_manager, from when the button sat
        # on the card; it is on the account form now, which no manager can open
        # - and a route left one rank wider than the only screen that reaches
        # it is a door with no handle on this side and a handle on that one.
        if not access.is_owner():
            return access.deny()
        attachment = request.env['ir.attachment'].sudo().browse(
            attachment_id).exists()
        if not attachment or attachment.res_model != 'hr.employee':
            return request.redirect('/manage/team-screen?view=team')
        # Read her id BEFORE unlinking: res_id is gone with the row afterwards,
        # and the redirect would land on the cards instead of her form.
        employee_id = attachment.res_id
        attachment.unlink()
        return request.redirect(
            '/manage/team-screen?view=team&edit=%d' % employee_id)

    @http.route('/manage/team-screen/unsend', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def unsend(self, **post):
        """Sent by mistake. Takes it off the bells as well as off this list."""
        if not access.is_manager():
            return access.deny()
        record = request.env['modryn.announcement'].sudo().browse(
            int(post.get('announcement_id') or 0)).exists()
        if record:
            record.modryn_unsend()
        return request.redirect('/manage/team-screen?view=announce')


# The staff section, so a MANAGER gets it without the owner having to grant
# anything - modryn_can_view lets a manager into every staff-section page - and
# the owner can still hand it to a senior saleswoman by role. Not the manage
# section, which is owner-only by design and would shut every manager out of the
# screen named after her.
# The BOTTOM row, with the team, the dresses and the reports - the boutique's own
# administration rather than the shift in front of her. Grantable through the
# owner's matrix like everything else down there, so a senior saleswoman can be
# given it without being made a manager.
nav.register('boss', '/manage/team-screen', _lt("Manager screen"), 12,
             'manage', 'fa-bullhorn')
