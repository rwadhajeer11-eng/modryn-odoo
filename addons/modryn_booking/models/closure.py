from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynClosure(models.Model):
    """A date the boutique does not open, whatever its weekday says.

    modryn.opening.hours can say the shop opens on Thursdays. It cannot say it
    is shut on THIS Thursday, and until now nothing could — so Yom Kippur, a
    family wedding and stocktaking were all unsayable, and /book cheerfully sold
    slots on every one of them.

    A closed date behaves exactly like a weekday with no window: the day does
    not render at all. It is NOT a full day. A full day is still shown, with a
    waitlist form, because learning she could be first in line is worth
    something; a day nobody works has nothing to be first in line for. Closed
    and fully booked are different answers to the bride and collapsing them
    would be a regression, not a shortcut.

    Ships EMPTY, like modryn.task.template. Israeli holidays are entered as
    data, never computed: they move with the Hebrew calendar, and even given
    the dates a boutique decides its own — Rosh Hashana is two days for one
    owner and four with the weekend for the next, and half of them work the
    eve. A Hebrew-calendar dependency would answer a question nobody asked and
    still be wrong for this shop.
    """

    _name = 'modryn.closure'
    _description = 'Closure'
    _order = 'date_from desc'

    # The owner's own words ("Yom Kippur", "Dana's wedding"). Shown to nobody
    # else: the storefront simply omits the day rather than explaining itself.
    name = fields.Char(required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    active = fields.Boolean(default=True)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for closure in self:
            if closure.date_to < closure.date_from:
                raise ValidationError(_("A closure has to end on or after the day it starts."))

    @api.model
    def modryn_closed_dates(self, first_day, last_day):
        """Every closed date in that window, both ends INCLUSIVE, in ONE search.

        A set, so /book's day loop pays a lookup rather than a query. Asking
        modryn_is_closed() per day would put fourteen queries back on the
        boutique's busiest public page — the exact cost modryn_hours_by_weekday()
        exists to have removed.

        The domain is an OVERLAP test, not containment: a closure running from
        before the window into the middle of it closes the days it covers, and
        `date_from >= first_day` would miss every one of them.

        sudo() for opening hours' reason — /book is anonymous, and which days
        the shop is shut is written on its door.
        """
        closed = set()
        for closure in self.sudo().search([('date_from', '<=', last_day),
                                           ('date_to', '>=', first_day)]):
            # Clamped to the window on both sides. Unclamped, one owner typing
            # 2027 into date_to walks a day at a time through years of dates
            # the caller then throws away.
            day = max(closure.date_from, first_day)
            last = min(closure.date_to, last_day)
            while day <= last:
                closed.add(day)
                day += timedelta(days=1)
        return closed

    @api.model
    def modryn_is_closed(self, day_date):
        """Is the boutique shut on that one date? For /claim, which renders one day."""
        return bool(self.sudo().search_count([('date_from', '<=', day_date),
                                              ('date_to', '>=', day_date)]))
