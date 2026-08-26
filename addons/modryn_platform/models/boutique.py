import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# A boutique's number is four digits, and it is how MODRYN refers to a shop out
# loud — on an invoice, on the phone. Stored as a Char and not an Integer: 0042
# is a different string from 42 and must survive a round trip, which an Integer
# column cannot promise.
CODE_RE = re.compile(r'^\d{4}$')

# A shop may be owned by up to four people. Not a business rule so much as a
# shape: the register exists to be read at a glance, and a shop with eleven
# partners listed is a shop nobody reads.
MAX_PARTNERS = 4


class ModrynBoutique(models.Model):
    """One subscribing boutique, as the platform knows it.

    Deliberately NOT a read of the boutique's own database. Every field here is
    something MODRYN knows about a customer — its number, where it is, who signs
    for it, what it pays for. A boutique's live data stays inside its own
    isolated database, which is the whole point of one database per boutique.
    """

    _name = 'modryn.boutique'
    _description = 'A subscribing boutique'
    _order = 'code'

    code = fields.Char(string="Shop number", required=True, index=True,
                       help="Four digits. How MODRYN refers to this shop.")
    name = fields.Char(required=True)
    city = fields.Char()
    street = fields.Char()
    # The subdomain its tenant answers on, when it has been provisioned. Empty
    # for a shop that has signed but is not built yet — which is a real state,
    # and one the register has to be able to show.
    slug = fields.Char(string="Subdomain",
                       help="The tenant's hostname label, e.g. 'bella' for bella.modryn.co.il.")
    subscription_type_id = fields.Many2one('modryn.subscription.type',
                                           string="Subscription")
    partner_ids = fields.One2many('modryn.boutique.partner', 'boutique_id',
                                  string="Partners")
    note = fields.Text()
    # Archive rather than delete: a shop that leaves is history MODRYN still
    # bills against, and a deleted row takes its partners with it.
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code)',
                                   "That shop number is already taken.")

    @api.constrains('code')
    def _check_code(self):
        for shop in self:
            if not CODE_RE.match(shop.code or ''):
                raise ValidationError(_("A shop number is exactly four digits."))

    @api.constrains('partner_ids')
    def _check_partner_count(self):
        for shop in self:
            if len(shop.partner_ids) > MAX_PARTNERS:
                raise ValidationError(
                    _("A shop can list at most %d partners.") % MAX_PARTNERS)

    def _row(self):
        """Plain dicts for QWeb, the way every other MODRYN screen is fed."""
        self.ensure_one()
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'city': self.city or '',
            'street': self.street or '',
            'slug': self.slug or '',
            'subscription': self.subscription_type_id.name or '',
            'partners': [{'name': p.name, 'phone': p.phone or ''}
                         for p in self.partner_ids],
            'note': self.note or '',
            'active': self.active,
        }


class ModrynBoutiquePartner(models.Model):
    """A person who owns a share of a boutique, with the number you ring.

    Its own model rather than four pairs of columns on the shop: a shop with one
    partner then has one row instead of three empty pairs, and adding a fifth
    later is a constraint change rather than a migration.
    """

    _name = 'modryn.boutique.partner'
    _description = 'A boutique partner'
    _order = 'sequence, id'

    boutique_id = fields.Many2one('modryn.boutique', required=True,
                                  ondelete='cascade', index=True)
    name = fields.Char(required=True)
    phone = fields.Char()
    sequence = fields.Integer(default=10)
