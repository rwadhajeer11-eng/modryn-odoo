from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynQueueDay(models.Model):
    """How many appointments ONE DATE takes, hour by hour.

    THE DIFFERENCE FROM modryn.queue.hour, and it is the whole reason this
    model exists: that one is the week the boutique normally works — Sunday
    takes two at eleven, every Sunday, forever. This one is the Tuesday in
    March when the shop is at a wedding fair, the Thursday it opens late, the
    week of the holiday. A recurring pattern cannot say any of that, and
    making the owner edit the pattern twice — once to change it and once to
    change it back — is how a shop ends up shut in April because nobody
    remembered.

    THE DATE WINS WHERE IT SPEAKS. A date with any row at all is the whole
    answer for that date; a date with none falls back to the weekly pattern,
    and a boutique with no pattern falls back to its opening hours. Three
    layers, each one only consulted when the one above it is silent — which
    means an owner who never opens this screen keeps exactly the week she has.

    ZERO IS A REAL ANSWER here too, and it is the important one: writing zero
    against every hour of a date is how she says "we are open, and the website
    gives nothing away that day".
    """

    _name = 'modryn.queue.day'
    _description = 'How many online bookings one date takes'
    _order = 'day, hour'

    day = fields.Date(required=True, index=True)
    hour = fields.Float(required=True)
    how_many = fields.Integer(string="How many", default=0, required=True)

    @api.constrains('how_many')
    def _check_how_many(self):
        for row in self:
            if row.how_many < 0:
                raise ValidationError(
                    _("An hour cannot take a negative number of bookings."))

    @api.constrains('day', 'hour')
    def _check_one_row_per_hour(self):
        """One answer per hour of a date.

        Python and not a SQL unique index, for the reason modryn.queue.hour
        already records: `hour` is a float, and 11 and 11.0 are the same hour
        to a person and two different keys to Postgres.
        """
        for row in self:
            clash = self.search([
                ('id', '!=', row.id),
                ('day', '=', row.day),
                ('hour', '>=', row.hour - 0.001),
                ('hour', '<=', row.hour + 0.001),
            ], limit=1)
            if clash:
                raise ValidationError(_("That hour is already set for that day."))

    @api.model
    def modryn_days(self, first, last):
        """{date: {hour_float: how_many}} for every date she has spoken about.

        sudo() because the public booking page reads this as the anonymous
        website user — the same reason the rest of the booking models use it,
        and there is nothing here a customer is not about to be shown anyway.
        """
        found = {}
        for row in self.sudo().search([('day', '>=', first), ('day', '<=', last)]):
            found.setdefault(row.day, {})[round(row.hour, 4)] = row.how_many
        return found

    @api.model
    def modryn_on(self, day_date):
        """{hour_float: how_many} for one date, or an empty dict if she has
        said nothing about it. An empty dict is NOT "shut" — it means the
        weekly pattern answers instead."""
        return self.modryn_days(day_date, day_date).get(day_date, {})
