from odoo import fields, models


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
        return self
