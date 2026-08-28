from odoo import api, fields, models


class CalendarEvent(models.Model):
    """Marking an appointment SOLD is what takes a dress off the rail.

    Nothing in this product decremented stock before, because nothing in it
    sells: there is no sale.order, no stock.picking and no checkout - a sale is
    a field on the appointment. So the decrement had to be attached to
    something, and this is the only moment a dress actually leaves the shop.

    Attached to the OUTCOME rather than to a button of its own, for two
    reasons. The stylist already records the outcome, so the count costs her
    nothing extra and cannot be forgotten separately. And an outcome that is
    corrected - sold marked by mistake, then changed - puts the dress back,
    which a separate button would not.
    """

    _inherit = 'calendar.event'

    # Whether THIS appointment actually took a dress off the rail. Stored,
    # because putting one back is only honest if one was taken, and by the time
    # the outcome is corrected the count no longer remembers. Without it the
    # pair was asymmetric and invented stock: a dress sold while the count said
    # zero takes nothing (correctly), and correcting that outcome then handed
    # the boutique a dress it never owned. Measured: 0 -> take -> None -> put
    # back -> 1.
    modryn_stock_taken = fields.Boolean(default=False, copy=False, readonly=True)

    @api.model
    def _modryn_stock_relevant(self, vals):
        return 'modryn_outcome' in vals or 'modryn_variant_id' in vals

    def write(self, vals):
        if not self._modryn_stock_relevant(vals):
            return super().write(vals)

        # Read BEFORE, because the write is what changes them.
        before = {e.id: (e.modryn_outcome, e.modryn_variant_id) for e in self}
        res = super().write(vals)

        for event in self:
            was_outcome, was_variant = before.get(event.id, (None, None))
            was_sold = was_outcome == 'sold'
            is_sold = event.modryn_outcome == 'sold'

            if was_sold and was_variant and (
                    not is_sold or event.modryn_variant_id != was_variant):
                # It is no longer a sale of THAT dress - either the outcome was
                # corrected, or the stylist fixed which size it actually was.
                # Only give one back if one was actually taken: the sale may
                # have been recorded against a size the count already said was
                # empty, and putting a dress back on a rail it never left is
                # how a boutique ends up believing in stock it does not have.
                if event.modryn_stock_taken:
                    was_variant.modryn_put_one_back()
                    event.sudo().modryn_stock_taken = False

            if is_sold and event.modryn_variant_id and (
                    not was_sold or event.modryn_variant_id != was_variant):
                # A sale of a dress the count says is not there still happened,
                # and the right answer is a count that reads zero rather than a
                # refused sale - the owner sees the discrepancy on the
                # catalogue page. But whether one came off the rail is recorded,
                # so undoing this later gives back exactly what was taken and
                # nothing more.
                took = event.modryn_variant_id.modryn_take_one()
                event.sudo().modryn_stock_taken = took is not None
        return res
