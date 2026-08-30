from odoo import fields, models


def _relevant(vals):
    """Is this write one that could change what the rail holds?"""
    return 'modryn_outcome' in vals or 'modryn_variant_id' in vals


def _settle(record, was_outcome, was_variant):
    """Bring the count into line with an outcome that has just changed.

    `record` is anything carrying the three fields - a booking or a walk-in.
    The two arguments are what it said BEFORE the write, because the write is
    what changed them and there is no other way back to them afterwards.
    """
    was_sold = was_outcome == 'sold'
    is_sold = record.modryn_outcome == 'sold'

    if was_sold and was_variant and (
            not is_sold or record.modryn_variant_id != was_variant):
        # It is no longer a sale of THAT dress - either the outcome was
        # corrected, or the stylist fixed which size it actually was. Only give
        # one back if one was actually taken: the sale may have been recorded
        # against a size the count already said was empty, and putting a dress
        # back on a rail it never left is how a boutique ends up believing in
        # stock it does not have.
        if record.modryn_stock_taken:
            was_variant.modryn_put_one_back()
            record.sudo().modryn_stock_taken = False

    if is_sold and record.modryn_variant_id and (
            not was_sold or record.modryn_variant_id != was_variant):
        # A sale of a dress the count says is not there still happened, and the
        # right answer is a count that reads zero rather than a refused sale -
        # the owner sees the discrepancy on the catalogue page. But whether one
        # came off the rail is recorded, so undoing this later gives back
        # exactly what was taken and nothing more.
        took = record.modryn_variant_id.modryn_take_one()
        record.sudo().modryn_stock_taken = took is not None


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

    def write(self, vals):
        if not _relevant(vals):
            return super().write(vals)
        # Read BEFORE, because the write is what changes them.
        before = {e.id: (e.modryn_outcome, e.modryn_variant_id) for e in self}
        res = super().write(vals)
        for event in self:
            _settle(event, *before.get(event.id, (None, None)))
        return res


class QueueEntry(models.Model):
    """A walk-in can take a dress off the rail too.

    She was the gap in the original design: the outcome lived on calendar.event
    only, so a bride who walked in without an appointment - most of them - was
    finished with nothing recorded, and the dress she carried out was still on
    the count the next morning.

    The same three fields and the same rule, because it is the same event: a
    dress left the shop, or it did not. Recorded when the stylist closes her on
    the floor board, which is the one moment somebody actually knows.
    """

    _inherit = 'modryn.queue.entry'

    modryn_outcome = fields.Selection(
        selection=[('sold', 'Sold'), ('not_sold', 'Left without buying')],
        copy=False,
    )
    modryn_variant_id = fields.Many2one(
        'product.product', string="The dress she took", copy=False)
    modryn_stock_taken = fields.Boolean(default=False, copy=False, readonly=True)
    # WHEN it was recorded, which is the only honest date for "gowns sold on
    # Tuesday". The alternative is create_date, and a bride who walks in at ten
    # to six and buys at half past is not a Monday sale because she queued on a
    # Monday. The booking half has carried this since outcomes existed.
    modryn_outcome_at = fields.Datetime(
        string="When it was recorded", readonly=True, copy=False)

    def write(self, vals):
        if not _relevant(vals):
            return super().write(vals)
        before = {e.id: (e.modryn_outcome, e.modryn_variant_id) for e in self}
        # Stamped here rather than by every caller: there is one route that
        # writes an outcome today and there will be more, and a timestamp that
        # depends on being remembered is a timestamp that goes missing.
        if 'modryn_outcome' in vals and vals.get('modryn_outcome'):
            vals = dict(vals, modryn_outcome_at=fields.Datetime.now())
        res = super().write(vals)
        for entry in self:
            _settle(entry, *before.get(entry.id, (None, None)))
        return res
