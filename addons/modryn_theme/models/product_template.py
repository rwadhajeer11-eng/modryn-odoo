from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Bridal pricing is frequently "by consultation": a boutique shows the dress
    # but withholds the number until the client is in the room. Odoo's shop has
    # no concept of a priced-but-price-hidden product, so it is one field here.
    #
    # Deliberately per-dress, not a boutique-wide switch: MODRYN's owner toggle
    # matrix has a global equivalent, but the per-item flag is the one that
    # earns its keep — a boutique typically hides prices on couture and shows
    # them on evening wear.
    modryn_price_visible = fields.Boolean(
        string="Show price on website",
        default=True,
        help="When off, the storefront shows 'מחיר בתיאום' instead of the price.",
    )
