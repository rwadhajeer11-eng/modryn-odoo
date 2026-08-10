from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynGarmentPiece(models.Model):
    """A part of a garment that can be altered — hem, bodice, sleeves, train.

    Owner-maintained data, exactly like staff roles: boutiques that also alter
    veils, capes or menswear must not need a developer to say so.
    """

    _name = 'modryn.garment.piece'
    _description = 'Garment piece'
    _order = 'sequence, name'

    # Plain Char, NOT translate=True. A translatable field is stored as jsonb,
    # and uniqueness then compares whole JSON objects so the same piece typed in
    # two languages passes as distinct. This is one tenant's own data in one
    # tenant's own database — it is not UI chrome.
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # models.Constraint, NOT _sql_constraints — the latter was removed in Odoo 19
    # and silently produces no index at all.
    _name_uniq = models.Constraint('unique(name)', "That garment piece already exists.")

    @api.constrains('name')
    def _check_name(self):
        for piece in self:
            if piece.name and not piece.name.strip():
                raise ValidationError(_("A garment piece needs a name."))
