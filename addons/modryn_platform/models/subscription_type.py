from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynSubscriptionType(models.Model):
    """What a boutique pays for.

    A model and not a Selection, for modryn.staff.role's reason: the tiers are
    the platform owner's own commercial decision and will change without a
    developer. He said as much — "I'll define the subscription types later" —
    so the shipped rows are a starting point he can rename, archive or replace.
    """

    _name = 'modryn.subscription.type'
    _description = 'Subscription type'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    note = fields.Char(help="What this tier includes. Shown only to the platform owner.")
    active = fields.Boolean(default=True)

    # A Python constraint, not a SQL one: `name` is translate=True and therefore
    # jsonb, so unique(name) would compare whole JSON objects and let a visible
    # duplicate through — the trap modryn.staff.role documents at length.
    @api.constrains('name', 'active')
    def _check_name_unique(self):
        for tier in self:
            if not tier.name:
                continue
            if self.with_context(active_test=False).search_count([
                    ('id', '!=', tier.id), ('name', '=ilike', tier.name)]):
                raise ValidationError(_("That subscription type already exists."))

    # Which features this tier includes. Many2many, because a feature belongs to
    # as many tiers as the owner says — "Premium and Standard both include the
    # workshop" is the ordinary case, and duplicating the feature per tier would
    # mean renaming it in two places.
    feature_ids = fields.Many2many(
        'modryn.platform.feature',
        'modryn_subscription_feature_rel', 'type_id', 'feature_id',
        string="Includes")
