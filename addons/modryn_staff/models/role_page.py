from odoo import api, fields, models

from .. import nav

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'

# What a brand-new role may open until the owner says otherwise. Deliberately
# NOT the floor board — the product decision this build implements is that
# plain staff live on their own page unless the owner grants more.
DEFAULT_PAGE_KEYS = ('roster', 'checkin')

# Pages the matrix may never take away. Kept as a tuple rather than repeated
# literals so modryn_can_view and the owner's tick-list cannot disagree - and
# they were two separate 'home' literals in two files before this.
ALWAYS_OPEN = ('home', 'profile')


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
    def modryn_unread(self):
        """Unread count for the navbar bell, for the signed-in woman.

        Lives here because the navbar is rendered by staff_layout, which every
        page wears - and QWeb needs ONE call it can make without each controller
        remembering to put a count in its own render context. A page that forgot
        would show a bell that is permanently empty, and nothing would report it.
        """
        user = self.env.user
        if not user or user._is_public() or not user.has_group(GROUP_STAFF):
            return 0
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)
        return self.env['modryn.staff.notification'].sudo().modryn_unread_count(
            employee)

    @api.model
    def modryn_page_title(self, page_key):
        """The browser-tab title for a page, in the reader's language.

        Read off the nav registry rather than written a second time: every page
        already has a translated label there, and a title maintained separately
        is a title that drifts from the tab it sits next to.

        It exists because a QWeb template's `name` attribute lands in
        ir.ui.view.name, which is a plain varchar and NOT translatable - so every
        browser tab on this Hebrew-first product read English ("MODRYN staff
        home") with no way to translate it. Odoo's website layout takes
        `additional_title` from the render context if the page supplies one, so
        supplying it here fixes every page at once.

        Returns False, not '', when there is no such page: the layout tests it
        with `if not additional_title`, and an empty string would take the same
        branch while reading as a title somebody meant to be blank.
        """
        entry = nav.page(page_key) if page_key else None
        # str(), because the label is a LazyTranslate that only resolves when
        # something asks for its text - and the concatenation in the website
        # layout would otherwise stringify it at a point we do not control.
        return str(entry['label']) if entry else False

    def modryn_can_view(self, page_key):
        """May the signed-in user open this page?

        Owner: everything. Manager: every staff-section page. Staff: her home
        always, plus whatever the matrix grants her role. No role: home only.
        Manage-section pages never leave the owner, whatever the matrix says.
        """
        user = self.env.user
        if not user or user._is_public() or not user.has_group(GROUP_STAFF):
            return False
        if page_key in ALWAYS_OPEN:
            # Never configurable away. 'home' so no matrix state can strand a
            # signed-in staff member, and 'profile' because a woman correcting
            # her own phone number is not a privilege an owner grants - and the
            # boutique's ability to reach her depends on it being right.
            return True
        if user.has_group(GROUP_OWNER):
            return True
        entry = nav.page(page_key)
        if entry is None:
            return False
        # The two that would let a tick grant the power to tick. Owner-only
        # whatever the matrix holds - and the check is here, not only on the
        # screen that draws the matrix, so a hand-made POST cannot reach them
        # either.
        if page_key in nav.NEVER_GRANTABLE:
            return False
        # A manager gets the whole top row without anybody granting anything.
        # The bottom row she needs granting for, the same as a saleswoman: it is
        # the boutique's own administration, and "she manages a shift" is not
        # the same claim as "she may change the catalogue".
        if entry['section'] == 'staff' and user.has_group(GROUP_MANAGER):
            return True
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)
        # The UNION of her roles' grants, not her first role's. A woman who is
        # a seamstress AND a saleswoman can open what either job opens - the
        # alternative is an owner having to remember which of her two roles is
        # listed first, which is not a thing the Team page even shows.
        roles = employee.modryn_role_ids if employee else None
        if not roles:
            return False
        # sudo(): portal users have no ACL on this model, deliberately —
        # the grant table is data ABOUT them, not data OF theirs.
        return bool(self.sudo().search_count([
            ('role_id', 'in', roles.ids), ('page_key', '=', page_key)]))

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
                    # .get, not []: a module registered before icons existed
                    # would otherwise KeyError and take the whole nav down.
                    'icon': entry.get('icon') or 'fa-circle-o',
                })
        return entries
