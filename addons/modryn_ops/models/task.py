import logging
from datetime import datetime, time, timedelta

import pytz
from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# A task half an hour past due on a boutique floor is a task nobody is going
# to remember unprompted. One poke, then the managers own it.
ESCALATE_AFTER_MINUTES = 30

TASK_TYPES = [
    ('opening', "Opening"),
    ('closing', "Closing"),
    ('follow_up', "Follow-up"),
    ('no_show_recovery', "No-show recovery"),
    ('adhoc', "Ad hoc"),
]

# Authoritative copy; schema_guard.py asserts it exists after install/upgrade.
ONE_INSTANCE_PER_DAY_INDEX = 'modryn_task_modryn_one_instance_per_day'


class ModrynTask(models.Model):
    """One piece of floor work: a checklist item, a follow-up call, an errand.

    Deliberately NOT mail.activity — staff and managers are portal users with
    no backend, so activities would be invisible to the exact people meant to
    do them. Same plain-dict, controller-authorised shape as the alteration
    task next door.
    """

    _name = 'modryn.task'
    _description = 'Floor task'
    _order = 'due_at asc, id asc'

    name = fields.Char(required=True)
    task_type = fields.Selection(TASK_TYPES, required=True, default='adhoc', index=True)
    # Empty means "whoever gets to it" — an opening checklist belongs to
    # whoever opens the boutique, not to a name picked a week in advance.
    employee_id = fields.Many2one('hr.employee', index=True, ondelete='set null')
    due_at = fields.Datetime(index=True)
    state = fields.Selection([('open', "Open"), ('done', "Done")],
                             default='open', required=True, index=True)
    done_at = fields.Datetime(readonly=True)
    done_by_id = fields.Many2one('hr.employee', ondelete='set null')

    # Who it is about, when it is about someone. Phone denormalised for the
    # same reason the atelier does it: the follow-up call happens from this
    # row, and partners can be merged or renamed later.
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    customer_name = fields.Char()
    customer_phone = fields.Char()
    # The booking whose outcome created this task — the thread the follow-up
    # completion KPI hangs on.
    event_id = fields.Many2one('calendar.event', ondelete='set null', index=True)

    # Checklist instances only.
    template_id = fields.Many2one('modryn.task.template', ondelete='set null', index=True)
    day = fields.Date()

    note = fields.Text()
    escalated_at = fields.Datetime()
    # Computed, not stored: "overdue" moves with the clock (atelier precedent).
    is_overdue = fields.Boolean(compute='_compute_is_overdue')

    # One checklist instance per template per day, enforced where two floor
    # boards loading at 10:00:00 cannot argue about it.
    _modryn_one_instance_per_day = models.UniqueIndex(
        "(template_id, day) WHERE template_id IS NOT NULL",
        "Today's checklist already has this item",
    )

    def _compute_is_overdue(self):
        now = fields.Datetime.now()
        for task in self:
            task.is_overdue = bool(task.due_at and task.due_at < now and task.state == 'open')

    # ------------------------------------------------------------------ done
    def action_done(self, employee):
        """Idempotent: two staff ticking the same checklist box is one tick."""
        self.ensure_one()
        if self.state == 'done':
            return True
        self.write({
            'state': 'done',
            'done_at': fields.Datetime.now(),
            'done_by_id': employee.id if employee else False,
        })
        self.env['modryn.audit.log'].modryn_log(
            record=self, label=_("Task done: %s") % self.name,
            field='state', old='open', new='done')
        self._modryn_notify_boards()
        return True

    def action_reopen(self):
        """Reopening REPLACES the completion record — done_by is cleared, and
        the audit row is what remembers who had ticked it. escalated_at stays:
        the room was already poked about this task once."""
        self.ensure_one()
        if self.state == 'open':
            return True
        previous = self.done_by_id.name or ''
        self.write({'state': 'open', 'done_at': False, 'done_by_id': False})
        self.env['modryn.audit.log'].modryn_log(
            record=self, label=_("Task reopened: %s") % self.name,
            field='state', old='done: %s' % previous, new='open')
        self._modryn_notify_boards()
        return True

    def _modryn_notify_boards(self):
        self.env['bus.bus']._sendone('modryn_queue', 'modryn_queue/update',
                                     {'kind': 'task'})

    def _row(self):
        self.ensure_one()
        due_local = ''
        if self.due_at:
            due_local = pytz.utc.localize(self.due_at).astimezone(TZ).strftime('%H:%M')
        return {
            'id': self.id,
            'name': self.name,
            'type': self.task_type,
            'state': self.state,
            'assignee_id': self.employee_id.id or False,
            'assignee': self.employee_id.name or '',
            'customer': self.customer_name or '',
            'phone': self.customer_phone or '',
            'due': due_local,
            'due_date': (pytz.utc.localize(self.due_at).astimezone(TZ).strftime('%d.%m')
                         if self.due_at else ''),
            'note': self.note or '',
            'overdue': self.is_overdue,
            'done_by': self.done_by_id.name or '',
        }

    # ------------------------------------------------------------ checklists
    @api.model
    def modryn_ensure_today(self):
        """Materialise today's checklist lazily, on floor-board read.

        No spawner cron on purpose (the roster's modryn_ensure_week pattern):
        a day nobody opens the floor board is a day the boutique was closed,
        and it should leave no orphan tasks behind. Concurrency-safe the same
        way /floor/room is — the partial unique index is the referee and the
        savepoint absorbs the loser.
        """
        today = datetime.now(TZ).date()
        templates = self.env['modryn.task.template'].search([])
        if not templates:
            return
        existing = {t.template_id.id for t in self.search([
            ('day', '=', today), ('template_id', 'in', templates.ids)])}
        for template in templates:
            if template.id in existing:
                continue
            hour = int(template.due_hour)
            minute = int(round((template.due_hour - hour) * 60)) % 60
            local_due = TZ.localize(datetime.combine(today, time(hour, minute)))
            due_utc = local_due.astimezone(pytz.utc).replace(tzinfo=None)
            try:
                with self.env.cr.savepoint():
                    self.create({
                        'name': template.name,
                        'task_type': template.kind,
                        'template_id': template.id,
                        'day': today,
                        'due_at': due_utc,
                    })
            except IntegrityError:
                # Another board's request won the race; its row is ours too.
                continue

    # ------------------------------------------------------------ escalation
    @api.model
    def _modryn_escalate_overdue(self):
        """Overdue-and-unescalated tasks poke the room, once per task.

        One summary text per manager per pass rather than one per task — five
        forgotten checklist items are one problem, not five texts. The
        escalated_at stamp is what makes 'once': a task never re-alerts, even
        if it is reopened later.
        """
        cutoff = fields.Datetime.now() - timedelta(minutes=ESCALATE_AFTER_MINUTES)
        tasks = self.search([
            ('state', '=', 'open'),
            ('escalated_at', '=', False),
            ('due_at', '!=', False),
            ('due_at', '<', cutoff),
        ])
        if not tasks:
            return
        tasks.write({'escalated_at': fields.Datetime.now()})

        managers = self.env['hr.employee'].sudo().search([
            ('modryn_level', 'in', ('manager', 'owner'))])
        names = ', '.join(tasks.mapped('name'))
        for manager in managers:
            phone = manager.work_phone or manager.mobile_phone
            if not phone:
                continue
            # Each manager reads the text in her own staff language.
            lang = (manager.user_id.lang or 'he_IL') if manager.user_id else 'he_IL'
            body = self.with_context(lang=lang)._modryn_escalation_body(names)
            ok, detail = self.env['modryn.sms'].send_async(phone, body)
            if not ok:
                _logger.warning('[modryn.ops] escalation SMS not queued for %s: %s',
                                manager.name, detail)
        self._modryn_notify_boards()
        _logger.info('[modryn.ops] escalated %d overdue task(s): %s', len(tasks), names)

    @api.model
    def _modryn_escalation_body(self, names):
        return _("Overdue on the floor board: %(names)s") % {'names': names}


class ModrynTaskTemplate(models.Model):
    """A checklist item the OWNER maintains as data — same decision as roles,
    garment pieces, fitting rooms and shift templates: boutiques invent their
    own routines, a developer should not be in that loop.

    Plain Char name, uniqueness in Python: translate=True stores jsonb and
    breaks uniqueness silently (.memory/odoo-traps.md §5)."""

    _name = 'modryn.task.template'
    _description = 'Recurring checklist item'
    _order = 'kind asc, sequence asc, id asc'

    name = fields.Char(required=True)
    kind = fields.Selection([('opening', "Opening"), ('closing', "Closing")],
                            required=True, default='opening')
    # Local wall-clock hour the item is due (11.5 = 11:30 Israel time). The
    # daily instance snapshots it in UTC at generation, so editing a template
    # never rewrites a day already underway — the roster's snapshot rule.
    due_hour = fields.Float(required=True, default=11.0)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.constrains('due_hour')
    def _check_due_hour(self):
        for template in self:
            if not 0 <= template.due_hour < 24:
                raise ValidationError(_("The hour must be between 0 and 24."))

    @api.constrains('name')
    def _check_name(self):
        # Same enforcement as roles, rooms, pieces and shift templates: two
        # active items with one name means two identical checklist rows every
        # morning, one of which is doomed to escalate as a phantom.
        for template in self:
            if self.search_count([('name', '=ilike', template.name),
                                  ('id', '!=', template.id)]):
                raise ValidationError(_("That checklist item already exists."))
