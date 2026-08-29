import logging
from datetime import timedelta

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

# How far back the manager's "still to close" nag looks. Bookings older than
# this predate the outcome feature (or a long holiday), and nagging about
# ancient history would bury the ones that still matter.
UNCLOSED_WINDOW_DAYS = 14

# When the follow-up work an outcome creates comes due. Seven days is
# BridalLive's own default for the did-not-purchase call; a no-show gets
# chased the next day, while the memory of the missed slot is fresh.
FOLLOW_UP_DAYS = 7
NO_SHOW_RECOVERY_DAYS = 1

OUTCOMES = [
    ('sold', "Sold"),
    ('not_sold', "Not sold"),
    ('no_show', "No-show"),
]

# Field -> human label for the audit diff. Only these earn a row: outcome
# edits change money and credit, and a stylist swap changes whose conversion
# the sale counts toward.
AUDITED_FIELDS = {
    'modryn_outcome': "Outcome",
    'modryn_sale_amount': "Sale amount",
    'modryn_sale_items': "Sale items",
    'modryn_outcome_note': "Outcome note",
    'modryn_outcome_by_id': "Closed by",
    'modryn_employee_id': "Stylist",
}


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # How the visit went, as the person watching the room saw it. 0 means nobody
    # has said - deliberately not 1, so "unrated" and "poor" cannot be confused
    # by anything that sorts or averages these later.
    modryn_visit_rating = fields.Integer(default=0, copy=False)
    modryn_visit_note = fields.Text(copy=False)


    modryn_outcome = fields.Selection(selection=OUTCOMES, index=True)
    modryn_outcome_at = fields.Datetime(readonly=True)
    # The CLOSER. The original stylist stays modryn_employee_id, so credit
    # rules (first-touch vs last-touch) remain a reporting choice, never a
    # schema migration.
    modryn_outcome_by_id = fields.Many2one('hr.employee', ondelete='set null')
    modryn_sale_amount = fields.Float()
    modryn_sale_items = fields.Text()
    modryn_outcome_note = fields.Char()
    modryn_feedback_sent_at = fields.Datetime(readonly=True)

    # ------------------------------------------------------------- audit diff
    def _modryn_audit_repr(self, field_name):
        self.ensure_one()
        value = self[field_name]
        field = self._fields[field_name]
        if field.type == 'many2one':
            return (value.name or '') if value else ''
        if field.type == 'selection':
            return dict(field.selection).get(value, '') if value else ''
        if value is False or value is None:
            return ''
        return str(value)

    def write(self, vals):
        watched = [f for f in AUDITED_FIELDS if f in vals]
        if not watched:
            return super().write(vals)
        bookings = self.filtered('modryn_is_booking')
        before = {e.id: {f: e._modryn_audit_repr(f) for f in watched} for e in bookings}
        result = super().write(vals)
        for event in bookings:
            for field_name in watched:
                new = event._modryn_audit_repr(field_name)
                if before[event.id][field_name] != new:
                    self.env['modryn.audit.log'].modryn_log(
                        record=event,
                        label=AUDITED_FIELDS[field_name],
                        field=field_name,
                        old=before[event.id][field_name],
                        new=new,
                    )
        return result

    # ---------------------------------------------------------------- outcome
    def modryn_set_outcome(self, outcome, employee, amount=0.0, items=None,
                           note=None, force=False):
        """Close a booking. Returns None on success, an error code otherwise.

        Strict about overwrites: changing a recorded outcome needs force,
        which the controller grants to managers only. A cancelled booking
        never gets an outcome — cancellation already IS its ending.
        """
        self.ensure_one()
        if not self.modryn_is_booking:
            return 'not_booking'
        if self.modryn_cancelled_at:
            return 'cancelled'
        if outcome not in dict(OUTCOMES):
            return 'invalid_outcome'
        previous = self.modryn_outcome
        if previous and not force:
            return 'already_set'

        values = {
            'modryn_outcome': outcome,
            'modryn_outcome_at': fields.Datetime.now(),
            'modryn_outcome_by_id': employee.id if employee else False,
            'modryn_outcome_note': (note or '').strip(),
        }
        if outcome == 'sold':
            values['modryn_sale_amount'] = float(amount or 0.0)
            values['modryn_sale_items'] = (items or '').strip()
        else:
            # An overwrite away from 'sold' must not leave money on the row.
            values['modryn_sale_amount'] = 0.0
            values['modryn_sale_items'] = False
        self.write(values)

        # Side effects belong to a CHANGED outcome. A force re-save that only
        # corrects a detail (amount, items, note) must not re-text the bride,
        # reset the follow-up task's clock, or re-mint anything — the write
        # above already recorded and audited the correction.
        if previous != outcome:
            if previous:
                self._modryn_outcome_tasks_cancel()
            self._modryn_outcome_flow()

        # One realtime mechanism for the whole floor: every open board
        # refreshes, and the card shows its outcome badge everywhere at once.
        self.env['bus.bus']._sendone('modryn_queue', 'modryn_queue/update', {
            'kind': 'booking_outcome', 'event_id': self.id, 'outcome': outcome,
        })
        return None

    def _modryn_outcome_flow(self):
        self.ensure_one()
        outcome = self.modryn_outcome
        if outcome == 'sold':
            self._modryn_outcome_sms('thank_you')
        elif outcome == 'not_sold':
            # Always sent, by the owner's decision — and stamped, so a manager
            # sees the touchpoint happened without digging in the outbox. The
            # stamp means "queued": delivery state lives on the outbox row.
            if self._modryn_outcome_sms('feedback'):
                self.modryn_feedback_sent_at = fields.Datetime.now()
        elif outcome == 'no_show':
            self._modryn_outcome_sms('rebook')
        self._modryn_refresh_partner_category()
        self._modryn_outcome_tasks_create()

    def _modryn_outcome_sms(self, kind):
        self.ensure_one()
        if not self.modryn_customer_phone:
            return False
        body = self._modryn_sms_env()._modryn_body(kind)
        ok, detail = self.env['modryn.sms'].send_async(self.modryn_customer_phone, body)
        if not ok:
            _logger.warning('[modryn.ops] %s SMS not queued for %s: %s',
                            kind, self.id, detail)
        return ok

    # ------------------------------------------------------- follow-up tasks
    def _modryn_task_customer(self):
        """Name + phone the task denormalises. The partner lookup swaps the
        booking title's 'Fitting: …' prefix for her actual name when it can."""
        self.ensure_one()
        partner = None
        if self.modryn_customer_phone:
            partner = self.env['res.partner'].sudo().search(
                [('phone', '=', self.modryn_customer_phone)], limit=1)
        return (partner.name if partner else self.name,
                self.modryn_customer_phone or '',
                partner.id if partner else False)

    def _modryn_outcome_tasks_create(self):
        self.ensure_one()
        outcome = self.modryn_outcome
        if outcome not in ('not_sold', 'no_show'):
            return
        customer, phone, partner_id = self._modryn_task_customer()
        # The original stylist owns the relationship; the closer only steps in
        # when the booking never had one.
        assignee = self.modryn_employee_id or self.modryn_outcome_by_id
        if outcome == 'not_sold':
            values = {
                'name': _("Follow up: %s") % customer,
                'task_type': 'follow_up',
                'due_at': fields.Datetime.now() + timedelta(days=FOLLOW_UP_DAYS),
                'note': self.modryn_outcome_note or '',
            }
        else:
            values = {
                'name': _("Rebook after no-show: %s") % customer,
                'task_type': 'no_show_recovery',
                'due_at': fields.Datetime.now() + timedelta(days=NO_SHOW_RECOVERY_DAYS),
            }
        values.update({
            'employee_id': assignee.id if assignee else False,
            'partner_id': partner_id,
            'customer_name': customer,
            'customer_phone': phone,
            'event_id': self.id,
        })
        self.env['modryn.task'].sudo().create(values)

    def _modryn_outcome_tasks_cancel(self):
        """An overwritten outcome invalidates the follow-up work the old one
        created. Unlinked rather than closed: the task was generated, not
        done, and a 'done' it never earned would flatter the completion KPI."""
        self.ensure_one()
        self.env['modryn.task'].sudo().search([
            ('event_id', '=', self.id),
            ('state', '=', 'open'),
            ('task_type', 'in', ('follow_up', 'no_show_recovery')),
        ]).unlink()

    def _modryn_refresh_partner_category(self):
        """Category is DERIVED from her whole outcome history, recomputed on
        every close (occupancy's derived-not-stored rule, applied to CRM).

        This is what makes two things true at once: a bride who bought keeps
        'purchased' through any later not-sold browse (some OTHER booking of
        hers is sold), and a manager force-correcting a mis-recorded sale on
        the one booking she has genuinely downgrades her (no sold row left).
        A blind ratchet could not tell those apart."""
        self.ensure_one()
        phone = self.modryn_customer_phone
        if not phone:
            return
        partner = self.env['res.partner'].sudo().search(
            [('phone', '=', phone)], limit=1)
        if not partner:
            return
        Event = self.env['calendar.event'].sudo().with_context(active_test=False)
        base = [('modryn_is_booking', '=', True),
                ('modryn_customer_phone', '=', phone)]
        if Event.search_count(base + [('modryn_outcome', '=', 'sold')]):
            category = 'purchased'
        elif Event.search_count(base + [('modryn_outcome', '=', 'not_sold')]):
            category = 'not_purchased'
        else:
            category = False
        if partner.modryn_category != category:
            partner.write({'modryn_category': category})

    # -------------------------------------------------------------- SMS copy
    def _modryn_body(self, kind):
        if kind not in ('thank_you', 'feedback', 'rebook'):
            return super()._modryn_body(kind)
        self.ensure_one()
        values = {
            'boutique': self.env.company.name,
            'book_link': '%s/book' % self._modryn_base_url(),
        }
        if kind == 'thank_you':
            return _("Thank you for visiting %(boutique)s! We're so happy for you — "
                     "we'll be in touch about the next steps.") % values
        if kind == 'feedback':
            return _("Thank you for visiting %(boutique)s! We'd love to hear how it "
                     "went — just reply to this message.") % values
        return _("We missed you today at %(boutique)s. Life happens! Book a new "
                 "time here: %(book_link)s") % values
