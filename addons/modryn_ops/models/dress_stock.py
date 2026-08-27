from odoo import api, models


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
                was_variant.modryn_put_one_back()

            if is_sold and event.modryn_variant_id and (
                    not was_sold or event.modryn_variant_id != was_variant):
                # Ignoring the None answer on purpose: a sale of a dress the
                # count says is not there still happened, and the correct
                # response is a count that says zero rather than a refused
                # sale. modryn_take_one leaves it at zero instead of going
                # negative, and the owner can see the discrepancy on the
                # catalogue page.
                event.modryn_variant_id.modryn_take_one()
        return res
