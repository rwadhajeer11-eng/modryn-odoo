from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynDressType(models.Model):
    """What KIND of thing this is - defined by the boutique, not by us.

    A model and not a Selection, for the same reason modryn.staff.role is one:
    boutiques do not agree on their own vocabulary. One sells נסיכה and מרמייד
    and calls a veil an accessory; the next stocks עדינה and צנועה and sells no
    accessories at all. A Selection would need a code change and a module
    upgrade every time an owner invents a category, which is not a thing she
    can ask for on a Tuesday.

    is_accessory is what separates "dresses" from "accessories" on the same
    page. It sits on the TYPE and not on each item, because it is a fact about
    the category - a veil is an accessory whoever is holding it - and putting
    it on the item lets two veils disagree.
    """

    _name = 'modryn.dress.type'
    _description = 'Kind of dress or accessory'
    _order = 'is_accessory, sequence, name'

    # translate=True makes this column jsonb. Odoo's choice, not ours, and
    # switching to a plain Char does NOT migrate the column - every create then
    # fails with InvalidTextRepresentation. Same note as modryn.staff.role.
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    # An accessory has no dress sizes: a veil is one size, and asking the owner
    # for 34/36/38 of it is a form that cannot be filled in honestly.
    is_accessory = fields.Boolean(
        string="This is an accessory, not a dress", default=False)
    # Archived, never deleted: items keep pointing at a retired type and their
    # history has to stay readable.
    active = fields.Boolean(default=True)

    # Python, not a SQL or models.Constraint one. Odoo 19 dropped
    # _sql_constraints entirely, and a modern unique() on a translate=True
    # column compares whole jsonb documents - so {"en_US": "Veil"} and
    # {"en_US": "Veil", "he_IL": "הינומה"} are "different" and a duplicate the
    # owner can plainly see sails through. Comparing the rendered value in her
    # language is what she means by "that one already exists".
    @api.constrains('name', 'active')
    def _check_name_unique(self):
        for kind in self:
            if not kind.name:
                continue
            clash = self.with_context(active_test=False).search([
                ('id', '!=', kind.id),
                ('name', '=ilike', kind.name),
            ], limit=1)
            if clash:
                raise ValidationError(_("That kind already exists."))
