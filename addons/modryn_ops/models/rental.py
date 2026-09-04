from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# How long after the wedding a gown may stay out before the shop calls it late.
# TEN DAYS, and a named constant rather than a 10 buried in three expressions:
# the number decides when a row turns red, when the banner appears, and what
# the banner counts, and those three must never be allowed to disagree.
GRACE_DAYS = 10


class ModrynRental(models.Model):
    """A gown that went out and is expected back.

    THE DIFFERENCE FROM A SALE, and it is the whole reason this is its own
    model: a sale is finished when the money is taken, and a rental is not
    finished until the dress is on the rail again. Everything here exists to
    answer one question the sale has no way to ask — "where is my dress" — and
    the answer has a deadline attached to it.

    TWO PRICES, both kept. What the gown is worth is not what she paid to
    borrow it, and a boutique needs both: the rental for the till, the ticket
    price for what is at risk while it is out of the shop. Storing only the
    rental would make a lost dress a number nobody could put back.

    THE WEDDING DATE IS THE CLOCK, not the day it was collected. A gown taken
    three weeks early for fittings is not late; the same gown a fortnight after
    the wedding is. So lateness is counted from the wedding and given ten days'
    grace, because a bride does not drive back the morning after.
    """

    _name = 'modryn.rental'
    _description = 'A gown out on rental'
    _order = 'returned_at asc, wedding_date asc, id desc'

    # Denormalised, exactly as modryn.sale keeps it, and for the same reason: a
    # bride is a name and a number on a slip, and this shop looks her up by
    # those two things and by nothing else.
    customer_name = fields.Char(required=True, index=True)
    customer_phone = fields.Char(index=True)

    # WHAT WENT OUT, said twice on purpose. The variant is the link back to the
    # rail; the written description is what survives a dress being deleted from
    # the catalogue years later, which is exactly when an old rental slip is
    # the thing somebody is reading.
    variant_id = fields.Many2one(
        'product.product', string="The gown", ondelete='set null', index=True)
    dress_label = fields.Char(string="What went out", required=True)
    dress_kind = fields.Char(string="Kind", index=True)

    retail_price = fields.Float(string="What it is worth")
    rental_price = fields.Float(string="What she paid to borrow it")

    employee_id = fields.Many2one(
        'hr.employee', string="Rented out by", ondelete='restrict', index=True)
    taken_at = fields.Datetime(
        string="Taken on", required=True, default=fields.Datetime.now, index=True)
    wedding_date = fields.Date(string="The wedding", required=True, index=True)
    returned_at = fields.Datetime(string="Back on the rail", index=True)
    note = fields.Char()

    # COMPUTED AND NOT STORED. "Late" changes as the clock moves, with nothing
    # writing to the row — a stored flag would be correct on the day it was
    # written and quietly wrong every day after until some cron nobody wrote
    # came along. Not searchable as a consequence, which is why the two places
    # that need to FIND late rentals build the date domain themselves through
    # modryn_late_domain() rather than filtering on this.
    is_late = fields.Boolean(
        string="Late back", compute='_compute_is_late')
    days_late = fields.Integer(
        string="Days late", compute='_compute_is_late')

    @api.depends('wedding_date', 'returned_at')
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for rental in self:
            if rental.returned_at or not rental.wedding_date:
                rental.is_late = False
                rental.days_late = 0
                continue
            over = (today - rental.wedding_date).days - GRACE_DAYS
            rental.is_late = over > 0
            rental.days_late = over if over > 0 else 0

    @api.model
    def modryn_late_domain(self):
        """The domain that finds every gown still out past its grace.

        Written once, here, because three callers need it — the banner, the
        search results and the count beside them — and three hand-rolled date
        comparisons is three chances to disagree about whether the tenth day
        itself counts.
        """
        cutoff = fields.Date.context_today(self) - timedelta(days=GRACE_DAYS)
        return [('returned_at', '=', False), ('wedding_date', '<', cutoff)]

    @api.constrains('retail_price', 'rental_price')
    def _check_prices(self):
        for rental in self:
            if rental.rental_price < 0 or rental.retail_price < 0:
                raise ValidationError(_("A price cannot be negative."))

    @api.constrains('wedding_date', 'taken_at', 'returned_at')
    def _check_dates(self):
        for rental in self:
            if rental.returned_at and rental.taken_at \
                    and rental.returned_at < rental.taken_at:
                raise ValidationError(
                    _("It cannot come back before it went out."))

    def modryn_mark_returned(self):
        """It is on the rail again. Idempotent: pressing twice must not move
        the date a second time, because the first press is the true one."""
        for rental in self:
            if not rental.returned_at:
                rental.returned_at = fields.Datetime.now()

    @api.model
    def modryn_search(self, query, limit=40):
        """Everything matching `query`, over all four things a person types.

        NAME, PHONE, THE GOWN, THE KIND — because those are the four ways a
        boutique remembers a rental, and which one somebody reaches for depends
        entirely on what they can remember. A search that only took a name
        would be useless to whoever is holding the dress and not the slip.
        """
        query = (query or '').strip()
        if len(query) < 2:
            return self.browse()
        like = '%%%s%%' % query
        return self.search([
            '|', '|', '|',
            ('customer_name', 'ilike', like),
            ('customer_phone', 'ilike', like),
            ('dress_label', 'ilike', like),
            ('dress_kind', 'ilike', like),
        ], limit=limit)

    def modryn_row(self):
        """One rental, as both screens read it.

        One builder for the server-rendered page and the as-you-type endpoint,
        so the two can never start disagreeing about what a row says — which is
        the ordinary way a live search and the page under it drift apart.
        """
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.customer_name or '',
            'phone': self.customer_phone or '',
            'dress': self.dress_label or '',
            'kind': self.dress_kind or '',
            'retail': round(self.retail_price or 0.0),
            'rental': round(self.rental_price or 0.0),
            'taken': self.taken_at.strftime('%d.%m.%Y') if self.taken_at else '',
            'wedding': self.wedding_date.strftime('%d.%m.%Y') if self.wedding_date else '',
            'returned': self.returned_at.strftime('%d.%m.%Y') if self.returned_at else '',
            'by': self.employee_id.name or '',
            'note': self.note or '',
            'late': self.is_late,
            'days_late': self.days_late,
        }
