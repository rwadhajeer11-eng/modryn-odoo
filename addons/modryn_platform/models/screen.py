from odoo import fields, models


class ModrynPlatformScreen(models.Model):
    """A screen a boutique can be sold.

    WHY THIS IS DATA AND NOT A READ OF THE PRODUCT. The boutique's screens are
    registered in modryn_staff's nav, which lives in a boutique's database —
    a database this module has never been able to reach, deliberately. So the
    platform keeps its own catalogue of what it sells.

    That means it can drift from what the product actually has, and the honest
    answer to that is not a clever sync: it is that the owner adds a screen here
    when the product gains one, the same way he adds a feature. The `key` is
    what makes a row checkable against reality later, if a check is ever
    written.
    """

    _name = 'modryn.platform.screen'
    _description = 'A screen a subscription can include'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    # The product's own nav key — 'boss', 'floor', 'atelier'. Not required, so a
    # screen can be listed before anybody looks up what the code calls it.
    key = fields.Char(
        help="The product's own name for this screen, e.g. 'floor'. "
             "Never shown to a boutique.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    section_ids = fields.One2many(
        'modryn.platform.section', 'screen_id', string="Boxes")


class ModrynPlatformSection(models.Model):
    """One box on a screen, sold separately from the rest of it.

    A tier that includes the manager's screen does not necessarily include
    every panel on it — the platform owner said as much: a plain tier gets the
    manager's screen with only the announcement and the team on it.

    A section belongs to exactly one screen, so this is a One2many and not a
    shared list: "the team" on the manager's screen is not the same box as
    anything called "the team" elsewhere, and merging them would let a tier
    grant a panel on a screen it does not sell.
    """

    _name = 'modryn.platform.section'
    _description = 'A box on a screen'
    _order = 'sequence, name'

    screen_id = fields.Many2one(
        'modryn.platform.screen', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True, translate=True)
    key = fields.Char(help="The product's own name for this box, if it has one.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
