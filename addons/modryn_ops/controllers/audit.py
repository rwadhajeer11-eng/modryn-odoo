from odoo import http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav

_lt = LazyTranslate(__name__)

GROUP_OWNER = 'modryn_staff.group_boutique_owner'
PAGE_SIZE = 50

nav.register('audit', '/manage/audit', _lt("Audit"), 80, 'manage')


class ModrynOpsAudit(http.Controller):
    """The owner's read of the audit trail.

    Owner-only on purpose — managers appear IN the log, so they do not get to
    read it. The model is append-only and reached exclusively through sudo();
    this page is its single UI.
    """

    @http.route('/manage/audit', type='http', auth='user', website=True, sitemap=False)
    def audit(self, page='1', **kw):
        user = request.env.user
        if not user or user._is_public() or not user.has_group(GROUP_OWNER):
            return request.not_found()
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1

        Audit = request.env['modryn.audit.log'].sudo()
        total = Audit.search_count([])
        rows = Audit.search([], limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        return request.render('modryn_ops.manage_audit', {
            'rows': [r._row() for r in rows],
            'page': page,
            'has_prev': page > 1,
            'has_next': page * PAGE_SIZE < total,
            'total': total,
            'active_tab': 'audit',
        })
