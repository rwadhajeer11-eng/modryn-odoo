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

import pytz

from odoo import http
from odoo.http import content_disposition, request
from odoo.tools.translate import LazyTranslate

from .. import nav
from . import access

_lt = LazyTranslate(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# How many past announcements the screen shows. Enough to see what the team was
# told this week and to unsend the one that was wrong; a manager who wants the
# whole history of a boutique is asking a different question than this screen.
RECENT = 20

# Ten megabytes, the figure the profile's own upload used before this screen
# took the job over. Read one byte PAST it rather than trusting a declared
# length: content-length is whatever the client says it is.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ModrynManagerScreen(http.Controller):

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _team(self):
        """Everyone who could be told something, in a stable order."""
        return request.env['hr.employee'].sudo().search(
            [('modryn_level', 'in', ('owner', 'manager', 'staff'))], order='name')

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

    def _team_rows(self):
        rows = []
        for employee in self._team():
            rows.append({
                'id': employee.id,
                'name': employee.name,
                'roles': ' · '.join(employee.modryn_role_ids.mapped('name')),
                'level': employee.modryn_level or '',
                'phone': employee.work_phone or '',
                'papers': [{
                    'id': doc.id,
                    'name': doc.name,
                    'at': doc.create_date.strftime('%d.%m.%Y')
                          if doc.create_date else '',
                } for doc in self._papers(employee)],
            })
        return rows

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
    VIEWS = ('announce', 'team')

    def _render(self, error=None, draft=None, picked=None, file_error=None,
                file_for=None, view=None):
        return request.render('modryn_staff.manager_screen', {
            # None means the tiles themselves. Anything unrecognised falls back
            # to them rather than 404ing: a stale link should land somewhere
            # useful, not on an error.
            'view': view if view in self.VIEWS else None,
            'team': [{'id': e.id, 'name': e.name} for e in self._team()],
            'team_rows': self._team_rows(),
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
        })

    @http.route('/manage/team-screen', type='http', auth='user', website=True,
                sitemap=False)
    def manager_screen(self, **kw):
        if not access.can_view('boss'):
            return access.deny()
        return self._render(view=kw.get('view'))

    @http.route('/manage/team-screen/announce', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def announce(self, **post):
        if not access.can_view('boss') or not access.is_manager():
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
    @http.route('/manage/team-screen/paper', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def paper_upload(self, **post):
        """File a signed page against one woman's record."""
        if not access.can_view('boss') or not access.is_manager():
            return access.deny()
        employee = request.env['hr.employee'].sudo().browse(
            int(post.get('employee_id') or 0)).exists()
        if not employee:
            return request.redirect('/manage/team-screen?view=team')

        upload = request.httprequest.files.get('paper')
        if not upload or not upload.filename:
            return self._render(file_error='nofile', file_for=employee.id,
                                view='team')
        data = upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return self._render(file_error='toobig', file_for=employee.id,
                                view='team')
        if not data:
            return self._render(file_error='nofile', file_for=employee.id,
                                view='team')

        request.env['ir.attachment'].sudo().create({
            'name': upload.filename,
            'raw': data,
            'res_model': 'hr.employee',
            'res_id': employee.id,
            # NEVER website-visible. public=True would put a woman's contract on
            # a URL anybody can fetch.
            'public': False,
        })
        return request.redirect('/manage/team-screen?view=team')

    @http.route('/manage/team-screen/paper/<int:attachment_id>', type='http',
                auth='user', website=True, sitemap=False)
    def paper_file(self, attachment_id, **kw):
        """Read one back. Through this route and never /web/content, which
        serves any attachment its ACL allows - and ir.attachment's rules are
        about models, not about which woman owns which payslip."""
        if not access.can_view('boss') or not access.is_manager():
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
        if not access.can_view('boss') or not access.is_manager():
            return access.deny()
        attachment = request.env['ir.attachment'].sudo().browse(
            attachment_id).exists()
        if attachment and attachment.res_model == 'hr.employee':
            attachment.unlink()
        return request.redirect('/manage/team-screen?view=team')

    @http.route('/manage/team-screen/unsend', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def unsend(self, **post):
        """Sent by mistake. Takes it off the bells as well as off this list."""
        if not access.can_view('boss') or not access.is_manager():
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
