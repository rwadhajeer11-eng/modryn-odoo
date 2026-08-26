from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynPlatformFeature(models.Model):
    """Something a subscription can include.

    A MODEL and not a Selection, deliberately — the same reasoning as
    modryn.staff.role. The platform owner said it himself: he will decide what
    the features are later. A Selection would mean a developer, a code change
    and a module upgrade every time he invents one, which is precisely the shape
    of thing that never gets invented.

    `code` exists so that the product can eventually ASK ("does this shop's
    subscription include the workshop?") without matching on a display name the
    owner is free to rename or translate. Nothing reads it yet; it is here so
    that the first thing which does will not have to migrate the table first.
    """

    _name = 'modryn.platform.feature'
    _description = 'A feature a subscription can include'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    # Machine name. Not required: the owner is filling in a commercial list, not
    # writing code, and forcing him to invent a slug for every line is how the
    # list stops being filled in.
    code = fields.Char(help="Optional short name for the product to check against, "
                            "e.g. 'workshop'. Never shown to a boutique.")
    note = fields.Char(help="What this covers, in your words.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # Python, not SQL: `name` is translate=True and therefore jsonb, so
    # unique(name) compares whole JSON objects and lets a visible duplicate
    # through — the trap modryn.staff.role documents at length.
    @api.constrains('name', 'active')
    def _check_name_unique(self):
        for feature in self:
            if not feature.name:
                continue
            if self.with_context(active_test=False).search_count([
                    ('id', '!=', feature.id), ('name', '=ilike', feature.name)]):
                raise ValidationError(_("That feature already exists."))

    @api.constrains('code', 'active')
    def _check_code_unique(self):
        for feature in self:
            if not feature.code:
                continue
            if self.with_context(active_test=False).search_count([
                    ('id', '!=', feature.id), ('code', '=ilike', feature.code)]):
                raise ValidationError(_("That feature code is already used."))
