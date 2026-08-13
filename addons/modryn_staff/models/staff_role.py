from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .role_page import DEFAULT_PAGE_KEYS


class ModrynStaffRole(models.Model):
    """A job role, defined by the boutique owner rather than by us.

    Deliberately a model and not a Selection field: boutiques do not agree on
    what jobs exist. One wants מוכרת בכירה and מלווה כלות, the next wants only
    מוכרת. A Selection would mean a code change and a module upgrade every time
    an owner invents a job title.
    """

    _name = 'modryn.staff.role'
    _description = 'Boutique staff role'
    _order = 'sequence, name'

    # translate=True, so this column is jsonb. That is Odoo's choice, not ours:
    # switching it to a plain Char does NOT migrate the existing column, and the
    # mismatch makes every create fail with InvalidTextRepresentation.
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    # Archive rather than delete: employees keep pointing at a retired role, so
    # historical assignments stay readable.
    active = fields.Boolean(default=True)
    # Members of this role take the workshop's auto-assigned alteration queue.
    # A flag on the ROLE, not the employee: the owner already curates roles,
    # and "the seamstresses do workshop work" is a fact about the job, not
    # about Rivka. The atelier reads it through hr.employee.modryn_role_id.
    is_workshop = fields.Boolean(default=False)

    @api.model_create_multi
    def create(self, vals_list):
        roles = super().create(vals_list)
        # Every new role starts with the same modest grants (see
        # DEFAULT_PAGE_KEYS for why not the floor board). Seeded at create so
        # both doors are covered: the XML seed roles at install, and every
        # role the owner invents later. Roles that PREDATE the matrix get the
        # same rows once, from migrations/19.0.1.6.0/post-migrate.py.
        Page = self.env['modryn.role.page'].sudo()
        for role in roles:
            for key in DEFAULT_PAGE_KEYS:
                Page.create({'role_id': role.id, 'page_key': key})
        return roles

    # A PYTHON constraint, not a SQL one. Two reasons, both learned the hard way:
    #  1. Odoo 19 removed `_sql_constraints` outright — a model still declaring
    #     it gets no index, so the rule does not exist. Odoo does log a warning
    #     at registry build, but it scrolls past in the boot log with nothing
    #     tying it to the duplicate you later find in the data.
    #  2. Even the modern models.Constraint('unique(name)') compares whole jsonb
    #     objects on a translatable column, so {"en_US": "x"} and
    #     {"en_US": "x", "he_IL": "x"} are "different" and a visible duplicate
    #     sails through.
    # Comparing the rendered value in the current language is what a human means
    # by "that role already exists".
    @api.constrains('name', 'active')
    def _check_name_unique(self):
        for role in self:
            if not role.name:
                continue
            clash = self.with_context(active_test=False).search([
                ('id', '!=', role.id),
                ('name', '=ilike', role.name),
            ], limit=1)
            if clash:
                raise ValidationError(_("That role already exists."))
