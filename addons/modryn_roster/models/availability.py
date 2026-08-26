from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .shift_slot import week_start as week_start_of
from .shift_template import shift_type_selection


class ModrynAvailability(models.Model):
    """"I can work Friday evening." One row per person, per day, per part of day.

    Re-keyed from (slot_id, employee_id). It used to hang off a modryn.shift.slot,
    which meant she could only offer a shift the boutique had already invented:
    with five templates on the books, the page showed five things to press, no
    Friday, no Saturday and no evening anywhere. That is backwards. What she is
    declaring is a fact about HER OWN calendar - "I am free Friday evening" - and
    it is true whether or not the shop has decided to open then.

    So the fact is stored as itself, and the connection to a real shift is made
    at READ time: whoever offered (slot.day, slot's shift_type) is available for
    that slot. One table, one resolution path, nothing to drift.

    The MODEL NAME is deliberately unchanged. security/ir.model.access.csv names
    it, i18n/he.po carries its constraint message, and verify.sh reads the table
    directly - renaming would break all three for no gain.
    """

    _name = 'modryn.availability'
    _description = 'Availability for a shift'
    _order = 'day, shift_type, employee_id'

    # Denormalised from `day`, and only ever written alongside it - every
    # create in this file computes it with week_start_of(day), and
    # _check_week_start below refuses any row where the two disagree.
    # It exists because every read on this table is "the whole week for this
    # person" and a range scan on two dates is worse than an equality on one.
    week_start = fields.Date(required=True, index=True)
    day = fields.Date(required=True, index=True)
    shift_type = fields.Selection(
        selection=lambda self: shift_type_selection(), required=True, index=True)
    employee_id = fields.Many2one(
        'hr.employee', required=True, ondelete='cascade', index=True)

    # Keyed on (day, shift_type, employee) and NOT on week_start as well:
    # week_start is derived from day, so including it would let the same person
    # hold the same cell twice under two different week_start values - a
    # duplicate the unique index would happily allow while looking like it
    # forbade one. The msgid text below is byte-identical to the old
    # constraint's so i18n/he.po's Hebrew carries over.
    _day_type_employee_uniq = models.Constraint(
        'unique(day, shift_type, employee_id)',
        "She has already offered to work that shift.")

    @api.constrains('week_start', 'day')
    def _check_week_start(self):
        for row in self:
            if row.day and row.week_start != week_start_of(row.day):
                raise ValidationError(
                    _("That day is not inside that week."))

    # ALL FOUR stored fields are listed, not just one. Odoo fires a constrains
    # only when a LISTED field is written, so a shorter list here is a guard
    # that silently never runs - which is exactly how the old
    # @api.constrains('slot_id') would have behaved after the re-key: no error,
    # just a published week that quietly accepted edits again.
    @api.constrains('week_start', 'day', 'shift_type', 'employee_id')
    def _check_not_published(self):
        for row in self:
            if row.week_start and self.env['modryn.roster.week'].sudo(
                    ).modryn_is_frozen(row.week_start):
                raise ValidationError(_(
                    "That week is already published - ask your manager to change it."))

    # ------------------------------------------------------------------ writes
    @api.model
    def modryn_toggle(self, employee, day, shift_type):
        """Offer a day-and-part, or withdraw the offer. Idempotent either way.

        Returns (ok, code, message). The CODE is a stable string the load test
        can put on its known-refusals list; the MESSAGE is the translated
        sentence a person reads. A Hebrew sentence can never be matched on by a
        test, which is why the two are separate.
        """
        Week = self.env['modryn.roster.week'].sudo()
        start = week_start_of(day)
        if Week.modryn_is_frozen(start):
            return False, 'published', _(
                "That week is already published - ask your manager to change it.")

        existing = self.sudo().search([
            ('day', '=', day), ('shift_type', '=', shift_type),
            ('employee_id', '=', employee.id)], limit=1)
        if existing:
            existing.unlink()
            return True, None, None
        self.sudo().create({
            'week_start': start, 'day': day,
            'shift_type': shift_type, 'employee_id': employee.id,
        })
        return True, None, None

    # ------------------------------------------------------------------ reads
    @api.model
    def modryn_week_map(self, start):
        """{(day, shift_type): [employee ids]} for one whole week, in ONE query.

        The old code ran a search plus a search_count PER SLOT - about fifteen
        queries to draw five cards, and it would have been sixty-three to draw
        twenty-one. Nothing in the gate asserts a query count, so the only
        symptom would have been a page that got slower and slower with no
        explanation.
        """
        rows = self.sudo().search([('week_start', '=', start)])
        out = {}
        for row in rows:
            out.setdefault((row.day, row.shift_type), []).append(row.employee_id.id)
        return out
