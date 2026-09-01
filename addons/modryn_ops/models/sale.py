from odoo import api, fields, models

# How a discount is expressed. Both, because a boutique says both: "ten percent
# for the sister of a bride we already dressed" and "take two hundred off, the
# hem is marked". Storing only one and converting would lose which was meant,
# and the owner reading the tracking screen wants the sentence she was told.
DISCOUNT_KINDS = [
    ('none', "No discount"),
    ('percent', "Percent off"),
    ('amount', "Amount off"),
]


class ModrynSale(models.Model):
    """A sale, as a thing in its own right.

    Until now this product had no sale. A dress leaving the shop was a FIELD on
    the visit that led to it — modryn_outcome on an appointment, or on a
    walk-in — which answered "did she buy" and "how much" and nothing else. It
    could not answer what else went in the bag, what the discount was for, or
    who took the hem up, because there was nowhere to put any of it.

    So: a sale with lines. One customer, one moment, one person who sold it, and
    as many things as she actually bought.

    NOT an Odoo sale.order, deliberately. That model brings pricelists, taxes,
    delivery, invoicing and a state machine this boutique has no use for, and
    every one of them is a screen somebody has to be taught. What a bridal shop
    needs recorded is who bought what, for how much, why it was cheaper than the
    ticket, and who altered it.
    """

    _name = 'modryn.sale'
    _description = 'A sale'
    _order = 'sold_at desc, id desc'

    # Denormalised, not a res.partner link. A bride buying a gown is not
    # becoming a contact record with a customer file; she is a name and a number
    # on a receipt, and the shop looks her up by exactly those two things three
    # years later. The sales history already searches both.
    customer_name = fields.Char(required=True, index=True)
    customer_phone = fields.Char(index=True)

    employee_id = fields.Many2one(
        'hr.employee', string="Sold by", required=True, ondelete='restrict',
        index=True)
    sold_at = fields.Datetime(
        string="Sold on", required=True, default=fields.Datetime.now, index=True)

    line_ids = fields.One2many('modryn.sale.line', 'sale_id',
                               string="What she bought")

    # ------------------------------------------------------------- alteration
    # Recorded on the sale rather than only in the workshop, because the two
    # answer different questions. The workshop tracks work in progress; this is
    # the receipt, and "was anything done to this dress, and by whom" has to
    # survive on the sale even when the alteration was a five-minute hem nobody
    # ever opened a workshop task for.
    altered = fields.Boolean(string="Altered", default=False)
    alteration_note = fields.Char(string="What was altered")
    alteration_by_id = fields.Many2one(
        'hr.employee', string="Altered by", ondelete='set null')

    # --------------------------------------------------------------- discount
    discount_kind = fields.Selection(
        DISCOUNT_KINDS, string="Kind of discount", default='none',
        required=True)
    discount_value = fields.Float(string="Discount", default=0.0)
    # REQUIRED in the controller whenever a discount is given, and the whole
    # reason the manager's tracking screen can be read: a number off the price
    # with no sentence beside it is exactly the row she would have to go and ask
    # somebody about.
    discount_reason = fields.Char(string="Why")

    subtotal = fields.Float(
        string="Before the discount", compute='_compute_totals', store=True)
    discount_amount = fields.Float(
        string="What came off", compute='_compute_totals', store=True)
    total = fields.Float(
        string="What she paid", compute='_compute_totals', store=True)

    @api.depends('line_ids.price', 'discount_kind', 'discount_value')
    def _compute_totals(self):
        for sale in self:
            subtotal = sum(sale.line_ids.mapped('price'))
            if sale.discount_kind == 'percent':
                # Clamped at both ends: a typo of 150 must not hand money back,
                # and a negative one must not quietly raise the price.
                percent = min(max(sale.discount_value or 0.0, 0.0), 100.0)
                cut = subtotal * percent / 100.0
            elif sale.discount_kind == 'amount':
                cut = min(max(sale.discount_value or 0.0, 0.0), subtotal)
            else:
                cut = 0.0
            sale.subtotal = subtotal
            sale.discount_amount = cut
            sale.total = subtotal - cut

    def modryn_discount_sentence(self):
        """The discount as the shop would say it, or empty when there was none.

        Built here rather than in the template because two screens show it — the
        sales history and the owner's tracking page — and a second copy is a
        second thing to keep in step.
        """
        self.ensure_one()
        if self.discount_kind == 'percent' and self.discount_value:
            return '%g%%' % self.discount_value
        if self.discount_kind == 'amount' and self.discount_value:
            return '₪%s' % '{:,}'.format(int(round(self.discount_value)))
        return ''

    def modryn_take_stock(self):
        """Take each sold dress off the rail, once.

        The same rule the outcome flow settles by: a sale of a dress the count
        says is not there still happened, so it is recorded rather than refused
        — and whether one actually came off is remembered per line, so nothing
        is given back later that was never taken.
        """
        for line in self.line_ids:
            if line.stock_taken or not line.variant_id:
                continue
            if 'modryn_take_one' not in dir(line.variant_id):
                continue
            took = line.variant_id.modryn_take_one()
            line.sudo().stock_taken = took is not None


class ModrynSaleLine(models.Model):
    """One thing on the receipt: a gown, a veil, or a line somebody typed."""

    _name = 'modryn.sale.line'
    _description = 'A line on a sale'
    _order = 'id'

    sale_id = fields.Many2one(
        'modryn.sale', string="The sale", required=True,
        ondelete='cascade', index=True)
    # Optional: a boutique sells things that are not on the rail — a veil from a
    # box under the counter, a hire charge, a repair. Those get a description
    # and a price and no catalogue row, which is the honest record of what
    # happened rather than a product invented to make the form happy.
    variant_id = fields.Many2one(
        'product.product', string="From the rail", ondelete='set null')
    # Always filled, even when there is a variant: it is what the receipt SAID,
    # and a gown renamed next season must not rewrite a sale from last year.
    description = fields.Char(string="What it was", required=True)
    price = fields.Float(string="Price", required=True, default=0.0)
    stock_taken = fields.Boolean(
        string="Taken off the rail", default=False, readonly=True)
