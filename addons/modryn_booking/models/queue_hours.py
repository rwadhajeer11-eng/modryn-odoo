from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .opening_hours import weekday_selection

# What an hour offers before anybody has said otherwise. One, not zero: a
# boutique that has opened its doors and never opened this screen should take
# a booking, and a grid full of zeroes on the day the feature ships would shut
# every shop's website without anybody deciding to.
DEFAULT_PER_HOUR = 1


class ModrynQueueHour(models.Model):
    """How many appointments the WEBSITE may give away, hour by hour.

    THE DIFFERENCE FROM OPENING HOURS, and it is the whole reason this exists:
    opening hours say when the door is unlocked. This says how much of that
    time is on sale to strangers. A boutique open ten to six can want two
    online bookings at eleven, none at two because that hour is kept for brides
    who telephone, and four on Thursday evening. The old screen had one number
    for a whole window and called it "how many fittings at the same time",
    which could not say any of that.

    A ROW PER HOUR, not a range with a number on it. The manager is answering
    "how many at eleven" and the screen asks her exactly that; a range would
    make her split a window in three to say something about one hour of it.

    ZERO IS A REAL ANSWER and is why the row exists at all: it means the shop
    is open at that hour and simply does not hand it out online.
    """

    _name = 'modryn.queue.hour'
    _description = 'How many online bookings an hour takes'
    _order = 'weekday, hour'

    weekday = fields.Selection(weekday_selection, required=True)
    hour = fields.Float(required=True)
    how_many = fields.Integer(string="How many", default=DEFAULT_PER_HOUR,
                              required=True)

    _sql_constraints = []

    @api.constrains('how_many')
    def _check_how_many(self):
        for row in self:
            if row.how_many < 0:
                raise ValidationError(
                    _("An hour cannot take a negative number of bookings."))

    @api.constrains('weekday', 'hour')
    def _check_one_row_per_hour(self):
        """One answer per hour, or the grid contradicts itself.

        Python and not a SQL unique index: `hour` is a float and two rows
        written as 11 and 11.0 are the same hour to a person and two different
        keys to Postgres. Rounded to the minute before comparing, which is the
        precision the grid actually offers.
        """
        for row in self:
            clash = self.search([
                ('id', '!=', row.id),
                ('weekday', '=', row.weekday),
                ('hour', '>=', row.hour - 0.001),
                ('hour', '<=', row.hour + 0.001),
            ], limit=1)
            if clash:
                raise ValidationError(_("That hour is already in the list."))

    @api.model
    def modryn_grid(self):
        """{weekday_str: {hour_float: how_many}} — every answer she has given.

        sudo() because the public booking page reads this as the anonymous
        website user. There is nothing here to leak: it is the same information
        the page is about to draw as a list of times.
        """
        grid = {}
        for row in self.sudo().search([]):
            grid.setdefault(row.weekday, {})[round(row.hour, 4)] = row.how_many
        return grid


class ModrynCustomerKind(models.Model):
    """Who a visitor says she is when she asks for an appointment.

    THE LIST IS THE RULE. A boutique that only sees brides writes one line
    here, and the booking page then offers one thing to be; a boutique that
    also fits bridesmaids and mothers writes three. There is no separate
    "accepted" flag, because a kind the shop does not accept is a kind the shop
    does not write down — and one list is one thing to keep true.

    A MODEL AND NOT A SELECTION, the same reasoning as the dress kinds and the
    staff roles: boutiques do not agree on their own vocabulary, and a code
    change every time an owner invents a category is a category never invented.
    """

    _name = 'modryn.customer.kind'
    _description = 'A kind of visitor who may book'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    note = fields.Char(help="Shown under the name on the booking page.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.constrains('name', 'active')
    def _check_name_unique(self):
        for kind in self:
            if not kind.name:
                continue
            clash = self.with_context(active_test=False).search([
                ('id', '!=', kind.id), ('name', '=ilike', kind.name)], limit=1)
            if clash:
                raise ValidationError(_("That kind of visitor already exists."))
