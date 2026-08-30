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
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from .. import nav
from . import access

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

    def _team(self):
        """Everyone who could be told something, in a stable order."""
        return request.env['hr.employee'].sudo().search(
            [('modryn_level', 'in', ('owner', 'manager', 'staff'))], order='name')

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

    def _render(self, error=None, draft=None, picked=None):
        return request.render('modryn_staff.manager_screen', {
            'team': [{'id': e.id, 'name': e.name} for e in self._team()],
            'announcements': self._recent(),
            'error': error,
            # What she typed, handed back so a refusal never costs her the
            # message she had written.
            'draft': draft or '',
            'picked': picked or [],
            'active_tab': 'boss',
        })

    @http.route('/manage/team-screen', type='http', auth='user', website=True,
                sitemap=False)
    def manager_screen(self, **kw):
        if not access.can_view('boss') or not access.is_manager():
            return access.deny()
        return self._render()

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
                error='empty', draft=body, picked=employees.ids)
        record = request.env['modryn.announcement'].sudo().modryn_publish(
            body, employees, self._my_employee())
        if not record:
            # The only way to get here with a body: every person ticked is the
            # author herself. Said out loud rather than redirecting to a list
            # that would show nothing new and leave her wondering.
            return self._render(
                error='nobody', draft=body, picked=employees.ids)
        return request.redirect('/manage/team-screen')

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
        return request.redirect('/manage/team-screen')


# The staff section, so a MANAGER gets it without the owner having to grant
# anything - modryn_can_view lets a manager into every staff-section page - and
# the owner can still hand it to a senior saleswoman by role. Not the manage
# section, which is owner-only by design and would shut every manager out of the
# screen named after her.
nav.register('boss', '/manage/team-screen', _lt("Manager screen"), 18,
             icon='fa-bullhorn')
