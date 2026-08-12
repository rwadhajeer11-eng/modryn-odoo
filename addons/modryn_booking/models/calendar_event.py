from odoo import fields, models


class CalendarEvent(models.Model):
    """Fitting appointments ride on calendar.event rather than a new model.

    The boutique's staff already live in Odoo's Calendar; a bespoke model would
    mean rebuilding day/week views, reminders and staff assignment that
    calendar.event already has. The cost is that a calendar.event is a generic
    meeting, so the booking-specific columns below are what make it a fitting.
    """

    _inherit = 'calendar.event'

    modryn_is_booking = fields.Boolean(
        string="Boutique booking",
        default=False,
        index=True,
        help="Set on events created from the public storefront booking pages.",
    )
    modryn_booking_type = fields.Selection(
        selection=[
            ('dress', "Dress fitting"),
            ('consult', "Consultation"),
        ],
        string="Booking type",
    )
    # The dress path binds a VARIANT, not a template: "size 38 of Emily" is what
    # the boutique must pull off the rail, and the variant is the only thing
    # that carries the size.
    modryn_variant_id = fields.Many2one(
        'product.product',
        string="Dress / size",
        ondelete='set null',
    )
    # Which of the window's parallel fittings this booking occupies: 0, 1, …
    # up to capacity - 1. Not a room and not a stylist — purely the slot's
    # discriminator, so that modryn_portal's unique index can be widened from
    # (start) to (start, modryn_slot_seat) and let Postgres, not a controller's
    # read-then-write, decide who gets the last place. With capacity 1 every
    # booking sits on seat 0 and that index is exactly as strict as it was.
    #
    # Declared HERE rather than beside the index it serves because both create
    # paths must set it, and modryn_booking's controller cannot import from
    # modryn_portal — the dependency runs the other way.
    # No index=True: nothing ever filters or orders on the seat. Both readers
    # project it off a recordset already narrowed by (modryn_is_booking, start),
    # and the partial unique index leads with start. A standalone btree over a
    # hot column whose every row is 0 would be write cost with no reader.
    modryn_slot_seat = fields.Integer(
        string="Slot seat",
        default=0,
        required=True,
    )
    modryn_customer_phone = fields.Char(string="Customer phone")
    # Israeli boutiques run on phone numbers, and the PRD requires the client to
    # accept cancellation/no-show terms before the booking is final. Storing the
    # acceptance time (not a bare boolean) is what makes it evidence.
    modryn_terms_accepted_at = fields.Datetime(string="Terms accepted at")
