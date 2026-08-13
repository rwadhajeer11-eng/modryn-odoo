from odoo import _, api, fields, models


class ModrynFloorHelper(models.Model):
    """One helper's stint on one customer card.

    An explicit through-model rather than a bare many2many, because WHEN someone
    joined is load-bearing: when a primary leaves, the longest-serving helper
    steps up. A many2many reads in the comodel's _order — by employee name — so
    promotion silently picked whoever was alphabetically first, which is not a
    fact about the floor at all.
    """

    _name = 'modryn.floor.helper'
    _description = 'Helper assigned to a customer'
    _order = 'create_date asc, id asc'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    entry_id = fields.Many2one('modryn.queue.entry', ondelete='cascade', index=True)
    event_id = fields.Many2one('calendar.event', ondelete='cascade', index=True)

    _entry_uniq = models.Constraint(
        'unique(entry_id, employee_id)', "She is already helping with this walk-in.")
    _event_uniq = models.Constraint(
        'unique(event_id, employee_id)', "She is already helping with this appointment.")


class ModrynHelperMixin(models.AbstractModel):
    """Shared helper plumbing for the two kinds of customer card."""

    _name = 'modryn.helper.mixin'
    _description = 'Customer card with helpers'

    # Compute + inverse keeps every existing caller (board payloads, occupancy,
    # the (4,)/(3,)/(5,) writes the controllers already do) working unchanged
    # while join order lives in the through-model underneath.
    modryn_helper_ids = fields.Many2many(
        'hr.employee',
        string="Helpers",
        compute='_compute_modryn_helper_ids',
        inverse='_inverse_modryn_helper_ids',
        store=False,
    )

    def _helper_field(self):
        return 'entry_id' if self._name == 'modryn.queue.entry' else 'event_id'

    def _helper_links(self):
        """This card's helper stints, oldest first."""
        return self.env['modryn.floor.helper'].sudo().search(
            [(self._helper_field(), '=', self.id)])

    def _compute_modryn_helper_ids(self):
        for record in self:
            links = record._helper_links() if record.id else self.env['modryn.floor.helper']
            # Preserve join order: browse the ids in the order the links came back.
            record.modryn_helper_ids = [(6, 0, links.mapped('employee_id').ids)]

    def _inverse_modryn_helper_ids(self):
        Link = self.env['modryn.floor.helper'].sudo()
        for record in self:
            field = record._helper_field()
            existing = record._helper_links()
            wanted = set(record.modryn_helper_ids.ids)
            # Drop departures, keep survivors untouched so their create_date —
            # and therefore their place in the promotion order — survives.
            existing.filtered(lambda l: l.employee_id.id not in wanted).unlink()
            have = set(existing.exists().mapped('employee_id').ids)
            for employee_id in record.modryn_helper_ids.ids:
                if employee_id not in have:
                    Link.create({field: record.id, 'employee_id': employee_id})

    def modryn_oldest_helper(self):
        """The helper who has been on this card longest, or an empty recordset."""
        self.ensure_one()
        link = self._helper_links()[:1]
        return link.employee_id if link else self.env['hr.employee']


class CalendarEvent(models.Model):
    """Who is serving this appointment: one accountable primary, any helpers.

    The bridal-floor reality is a saleswoman styling while a seamstress pins —
    one card, several people. The primary is the accountable one; helpers come
    and go without changing who owns the customer.
    """

    _name = 'calendar.event'
    _inherit = ['calendar.event', 'modryn.helper.mixin', 'modryn.room.occupant.mixin']

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )
    ASSIGNMENT_FIELDS = ('modryn_employee_id', 'modryn_helper_ids')

    def _modryn_is_open(self):
        self.ensure_one()
        if not self.modryn_is_booking:
            return False
        if 'modryn_cancelled_at' in self._fields and self.modryn_cancelled_at:
            return False
        # An appointment holds its room until it is over, not for ever.
        return bool(self.stop) and self.stop >= fields.Datetime.now()

    def write(self, vals):
        # Old primaries, captured before the write — the SMS fires only on a
        # CHANGE to a non-null assignee, never on a re-drop of the same person.
        old_primary = {}
        if 'modryn_employee_id' in vals:
            old_primary = {e.id: e.modryn_employee_id.id for e in self}
        result = super().write(vals)
        # Assignment changes must reach every open floor board. The queue model
        # already owns the bus channel; bookings piggyback on the same one so
        # there is exactly one realtime mechanism to reason about.
        if any(f in vals for f in self.ASSIGNMENT_FIELDS):
            bookings = self.filtered('modryn_is_booking')
            if bookings:
                self.env['bus.bus']._sendone(
                    'modryn_queue', 'modryn_queue/update',
                    {'kind': 'booking_assignment', 'ids': bookings.ids},
                )
            if old_primary:
                for event in bookings:
                    employee = event.modryn_employee_id
                    if employee and employee.id != old_primary.get(event.id):
                        notify = self.env['modryn.staff.notify']
                        body = event.with_context(
                            lang=notify.modryn_lang(employee),
                        )._modryn_assignment_body()
                        notify.modryn_assigned(employee, body)
        return result

    def _modryn_assignment_body(self):
        self.ensure_one()
        return _("New customer for you: %s") % (self.name or '')


class ModrynQueueEntry(models.Model):
    """Who is serving this walk-in: same primary + helpers shape as bookings."""

    _name = 'modryn.queue.entry'
    _inherit = ['modryn.queue.entry', 'modryn.helper.mixin', 'modryn.room.occupant.mixin']

    modryn_employee_id = fields.Many2one(
        'hr.employee',
        string="Assigned to",
        index=True,
        ondelete='set null',
    )

    def _modryn_is_open(self):
        self.ensure_one()
        return self.state in ('waiting', 'called')

    def _payload(self):
        # Extend the existing bus payload so helper changes reach every open
        # board through the push modryn_queue_poc already wired up.
        payload = super()._payload()
        payload['employee_id'] = self.modryn_employee_id.id or False
        payload['employee_name'] = self.modryn_employee_id.name or ''
        payload['helper_ids'] = self.modryn_helper_ids.ids
        payload['helper_names'] = self.modryn_helper_ids.mapped('name')
        return payload

    @api.model
    def _assignment_changed_fields(self):
        return {'modryn_employee_id', 'modryn_helper_ids'}

    def write(self, vals):
        old_primary = {}
        if 'modryn_employee_id' in vals:
            old_primary = {e.id: e.modryn_employee_id.id for e in self}
        result = super().write(vals)
        # The base model only notifies on state changes; assignment changes are
        # just as visible on the board, so they push too.
        if self._assignment_changed_fields() & set(vals):
            self._notify_board()
        if old_primary:
            for entry in self:
                employee = entry.modryn_employee_id
                if employee and employee.id != old_primary.get(entry.id):
                    notify = self.env['modryn.staff.notify']
                    body = entry.with_context(
                        lang=notify.modryn_lang(employee),
                    )._modryn_assignment_body()
                    notify.modryn_assigned(employee, body)
        return result

    def _modryn_assignment_body(self):
        self.ensure_one()
        return _("New customer for you: %s") % (self.name or '')

    def modryn_assign(self, employee):
        """Dispatch this walk-in to a member of staff (legacy single-assign).

        Kept because the click-fallback and older callers use it; it now means
        "make her the primary and call the customer".
        """
        self.ensure_one()
        self.write({
            'modryn_employee_id': employee.id,
            'state': 'called',
        })
        return self
