from odoo import fields, models


class ModrynBoutiqueAccount(models.Model):
    """A sign-in the platform issued to a boutique.

    The real account lives in that boutique's own database. This is MODRYN's
    note of what it handed over, which is the thing nobody could answer before
    without opening the shop's database.
    """

    _name = 'modryn.boutique.account'
    _description = 'A boutique sign-in the platform issued'
    _order = 'sequence, id'

    boutique_id = fields.Many2one(
        'modryn.boutique', string="Boutique", required=True,
        ondelete='cascade', index=True)
    username = fields.Char(string="Username", required=True)
    # READ BACK, not verified - see the module note. Restricted to the platform
    # owner's group so no other Odoo screen or export can show it, which is the
    # most that can be done for a value whose purpose is being legible.
    password = fields.Char(
        string="Password", groups='modryn_platform.group_platform_owner')
    # Whose account it is. A shop with three sign-ins needs to say which is the
    # owner's and which is the manager's, or the list answers nothing.
    holder = fields.Char(string="Who it belongs to")
    sequence = fields.Integer(default=10)
