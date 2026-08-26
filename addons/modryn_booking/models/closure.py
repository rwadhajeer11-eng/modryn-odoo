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

    # A closure used to be all-or-nothing, so "we shut at 14:00 on Thursday for
    # a wedding" was unsayable and the whole Thursday had to be sacrificed.
    #
    # full_day is a BOOLEAN rather than "start_hour is empty", because a Float
    # defaults to 0.0 and 0.0 is a legitimate midnight — the two states would be
    # indistinguishable the first time somebody typed a closure starting at 00:00.
    # Existing rows get True from the default when Odoo adds the column, so every
    # closure written before today keeps behaving exactly as it did.
    full_day = fields.Boolean(
        string="Closed all day", default=True,
        help="Uncheck to close only part of the day; the day still appears with "
             "its remaining hours.")
    start_hour = fields.Float(string="Closed from")
    end_hour = fields.Float(string="Closed until")

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for closure in self:
            if closure.date_to < closure.date_from:
                raise ValidationError(_("A closure has to end on or after the day it starts."))

    @api.constrains('full_day', 'start_hour', 'end_hour')
    def _check_hours(self):
        for closure in self:
            if closure.full_day:
                continue
            if closure.end_hour <= closure.start_hour:
                raise ValidationError(
                    _("A part-day closure has to end after it starts."))

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
        # full_day only. A part-day closure must NOT remove the date — the whole
        # point of it is that the morning is still for sale.
        for closure in self.sudo().search([('full_day', '=', True),
                                           ('date_from', '<=', last_day),
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
    def modryn_closed_hours(self, first_day, last_day):
        """Blocked hour ranges per date, for closures that shut only PART of a day.

        {date: [(from_hour, to_hour), ...]}. Full-day closures are deliberately
        absent: those delete the date through modryn_closed_dates(), and a day
        that never renders has no hours left to trim.

        ONE search for the whole window, for modryn_closed_dates()'s reason —
        /book's cost is a fixed number of queries, and asking per day would put
        the fourteen back. Same OVERLAP domain, and the same clamp, so a closure
        running in from outside the window still trims the days it covers without
        walking years of dates the caller throws away.
        """
        blocked = {}
        for closure in self.sudo().search([('full_day', '=', False),
                                           ('date_from', '<=', last_day),
                                           ('date_to', '>=', first_day)]):
            day = max(closure.date_from, first_day)
            last = min(closure.date_to, last_day)
            while day <= last:
                blocked.setdefault(day, []).append((closure.start_hour, closure.end_hour))
                day += timedelta(days=1)
        return blocked

    @api.model
    def modryn_is_closed(self, day_date):
        """Is the boutique shut ALL DAY on that date? For /claim, which renders one day.

        full_day only: the waitlist asks "is there anything to queue for here",
        and on a part-day closure there still is.
        """
        return bool(self.sudo().search_count([('full_day', '=', True),
                                              ('date_from', '<=', day_date),
                                              ('date_to', '>=', day_date)]))
