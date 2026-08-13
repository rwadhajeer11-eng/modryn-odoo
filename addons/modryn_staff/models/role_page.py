from odoo import api, fields, models

from .. import nav

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'

# What a brand-new role may open until the owner says otherwise. Deliberately
# NOT the floor board — the product decision this build implements is that
# plain staff live on their own page unless the owner grants more.
DEFAULT_PAGE_KEYS = ('roster', 'checkin')


class ModrynRolePage(models.Model):
    """One page a job role may open.

    Rows are the single source of truth — there is no "empty means default"
    fallback, because that would make "the owner unchecked everything"
    indistinguishable from "never configured". Defaults exist because they are
    SEEDED: create() on modryn.staff.role plants DEFAULT_PAGE_KEYS for every
    new role, and a migration does the same once for roles that predate the
    matrix.

    page_key is a plain Char on purpose (never translate=True): it must be
    SQL-comparable for the unique index, and it is a code, not a word.
    """

    _name = 'modryn.role.page'
    _description = 'Page a job role may open'

    role_id = fields.Many2one('modryn.staff.role', required=True,
                              ondelete='cascade', index=True)
    page_key = fields.Char(required=True)

    _role_page_uniq = models.UniqueIndex(
        '(role_id, page_key)', "That role already has this page.")

    # ------------------------------------------------------------------ read
    @api.model
    def modryn_can_view(self, page_key):
        """May the signed-in user open this page?

        Owner: everything. Manager: every staff-section page. Staff: her home
        always, plus whatever the matrix grants her role. No role: home only.
        Manage-section pages never leave the owner, whatever the matrix says.
        """
        user = self.env.user
        if not user or user._is_public() or not user.has_group(GROUP_STAFF):
            return False
        if page_key == 'home':
            # Always reachable: the one page that can never be configured away,
            # so no matrix state can strand a signed-in staff member.
            return True
        if user.has_group(GROUP_OWNER):
            return True
        entry = nav.page(page_key)
        if entry is None or entry['section'] == 'manage':
            return False
        if user.has_group(GROUP_MANAGER):
            return True
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)
        role = employee.modryn_role_id if employee else None
        if not role:
            return False
        # sudo(): portal users have no ACL on this model, deliberately —
        # the grant table is data ABOUT them, not data OF theirs.
        return bool(self.sudo().search_count([
            ('role_id', '=', role.id), ('page_key', '=', page_key)]))

    @api.model
    def modryn_nav(self):
        """The nav, filtered for the current user, pre-split by section.

        Plain dicts for QWeb; labels stay LazyGettext so they resolve in the
        page's language at render time.
        """
        entries = {'staff': [], 'manage': []}
        for entry in nav.PAGES:
            if self.modryn_can_view(entry['key']):
                entries[entry['section']].append({
                    'key': entry['key'],
                    'url': entry['url'],
                    'label': entry['label'],
                })
        return entries
