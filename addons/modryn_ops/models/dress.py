from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    """A dress, as the boutique thinks of one.

    Odoo already models the shape correctly: the TEMPLATE is the dress, and a
    product.product VARIANT is that dress in one size. This adds only what a
    bridal boutique needs on top and nothing Odoo already has - the photo,
    the name, the price and the size attribute were all here already.
    """

    _inherit = 'product.template'

    # A dress the boutique refers to out loud: "bring me 1042".
    #
    # A REAL stored column, and not related='default_code' as it started out.
    # product.template.default_code is not a column at all - it is a non-stored
    # compute over the variants, writable only when a template has exactly one
    # of them. Every dress here has three sizes, so writing it did nothing:
    # the owner typed a serial, the form said saved, and the value was silently
    # discarded. Measured on a fresh dress - three sizes, serial typed,
    # default_code came back False.
    #
    # Stored here also matches what the number MEANS. It names the dress, not
    # one size of it, and Odoo's field would have made "1042 in a 36" and
    # "1042 in a 38" separately editable and free to disagree.
    modryn_serial = fields.Char(string="Serial number", index=True)

    modryn_type_id = fields.Many2one(
        'modryn.dress.type', string="Kind",
        help="Which of the boutique's own categories this belongs to.")
    # Read off the type rather than stored again here: two facts about one thing
    # drift the moment somebody edits one of them.
    modryn_is_accessory = fields.Boolean(
        related='modryn_type_id.is_accessory', readonly=True)

    modryn_in_stock = fields.Integer(
        string="In stock", compute='_compute_modryn_in_stock',
        help="Across every size.")
    modryn_sold_out = fields.Boolean(compute='_compute_modryn_in_stock')

    @api.depends('product_variant_ids.modryn_stock')
    def _compute_modryn_in_stock(self):
        for template in self:
            total = sum(template.product_variant_ids.mapped('modryn_stock'))
            template.modryn_in_stock = total
            # Sold out is DERIVED, never a flag somebody sets. A stored flag
            # and a stock number are two facts about one thing, and they drift
            # the first time anybody edits one without the other.
            template.modryn_sold_out = total <= 0


class ProductProduct(models.Model):
    """One dress in one size - the thing that is actually in the shop or not."""

    _inherit = 'product.product'

    # NOT Odoo's stock module. This boutique has no warehouse, no picking and no
    # delivery: it has a rail with a number of dresses on it. Installing stock
    # to hold one integer per size would bring inventory valuation, procurement
    # rules and a routes engine, all of which would then need configuring or
    # they misbehave quietly.
    modryn_stock = fields.Integer(string="How many", default=0)

    @api.constrains('modryn_stock')
    def _check_stock(self):
        for variant in self:
            if variant.modryn_stock < 0:
                raise ValidationError(_("Stock cannot go below zero."))

    def modryn_take_one(self):
        """One left the rail. Returns what is left, or None if there were none.

        Guarded rather than clamped: a decrement that silently floors at zero
        turns "we sold a dress we did not have" into a number that looks right,
        and the whole reason to count is to notice that.
        """
        self.ensure_one()
        if self.modryn_stock <= 0:
            return None
        self.sudo().modryn_stock -= 1
        return self.modryn_stock

    def modryn_put_one_back(self):
        """A sale was undone. The exact mirror of modryn_take_one.

        Returns what is left, or None if there was nothing to give back.

        It has to be told whether one was ever taken, which is why the caller
        passes that in rather than this method assuming it. Unconditional, it
        INVENTED stock: mark an appointment sold for a dress the count says is
        not there (take_one correctly refuses and leaves it at zero), then
        correct the outcome - and the boutique now believes it owns a dress
        that never existed. Measured on a real variant: 0 -> take_one() -> None
        -> put_one_back() -> 1.
        """
        self.ensure_one()
        self.sudo().modryn_stock += 1
        return self.modryn_stock
