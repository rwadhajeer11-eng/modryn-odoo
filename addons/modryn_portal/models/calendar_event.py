import pytz

from odoo import fields, models

TZ = pytz.timezone('Asia/Jerusalem')


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # Cancelling never deletes. The boutique still needs to see that Tuesday
    # 14:00 was booked and dropped — no-show and cancellation history is exactly
    # what the deposit policy exists to price.
    modryn_cancelled_at = fields.Datetime(string="Cancelled at", readonly=True)
    modryn_cancelled_by = fields.Selection(
        selection=[('customer', "Customer"), ('boutique', "Boutique")],
        string="Cancelled by",
        readonly=True,
    )

    def modryn_cancel(self, by='customer'):
        """Release the slot without losing the record."""
        self.ensure_one()
        if self.modryn_cancelled_at:
            return self
        self.write({
            'modryn_cancelled_at': fields.Datetime.now(),
            'modryn_cancelled_by': by,
        })
        # The freed hour belongs to whoever asked for this day first. Offering
        # here — in the one method both the portal and the reminder link call —
        # means no cancellation path can forget to do it.
        local_day = pytz.utc.localize(self.start).astimezone(TZ).date()
        self.env['modryn.day.waitlist'].sudo().modryn_offer_next(local_day)
        return self
