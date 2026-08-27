from datetime import datetime

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

STATES = [
    ('intake', "Intake"),
    ('in_progress', "In progress"),
    ('ready', "Ready"),
    ('delivered', "Delivered"),
]
OPEN_STATES = ('intake', 'in_progress', 'ready')

# Digit keys ON PURPOSE: Selection columns are varchar, so the _order below
# compares text — '2' > '1' > '0' sorts High first, where 'high' > 'low' would
# sort alphabetically and put Low above Normal.
PRIORITIES = [
    ('0', "Low"),
    ('1', "Normal"),
    ('2', "High"),
]

# "Today" for the rota gate. Israeli boutiques and a UTC server disagree about
# the date for three hours every evening (.memory/odoo-traps.md §14).
TZ = pytz.timezone('Asia/Jerusalem')


class ModrynAlterationTask(models.Model):
    """One piece of alteration work on one customer's garment."""

    _name = 'modryn.alteration.task'
    _description = 'Alteration task'
    # Priority first, then the clock. Postgres puts NULL due dates last under
    # ASC, so legacy no-due tasks fall to the back of their priority band.
    _order = 'priority desc, due_date asc, id asc'

    # Who it is for. Phone is denormalised on purpose: the workshop calls the
    # customer directly, and a partner record may be merged or renamed later.
    partner_id = fields.Many2one('res.partner', string="Customer", ondelete='set null')
    customer_name = fields.Char(required=True)
    customer_phone = fields.Char()

    # What is being altered. The variant carries the size, which is what the
    # seamstress actually needs off the rail.
    variant_id = fields.Many2one('product.product', string="Dress / size",
                                 ondelete='set null')
    piece_ids = fields.Many2many('modryn.garment.piece', string="Pieces")
    note = fields.Text(string="Instructions")

    seamstress_id = fields.Many2one('hr.employee', string="Seamstress",
                                    index=True, ondelete='set null')
    state = fields.Selection(selection=STATES, default='intake', required=True, index=True)
    # Required by the shift manager at creation (the controller refuses a task
    # without it); the default only backfills rows that predate the field.
    priority = fields.Selection(PRIORITIES, default='1', required=True, index=True)
    # Nullable at the DB level — legacy rows have no due date and inventing one
    # would be data fiction — but required at the single creation door.
    due_date = fields.Date(string="Due")
    delivered_at = fields.Datetime(readonly=True)

    # Computed, not stored: "overdue" changes as the clock moves, so a stored
    # flag would be wrong from the moment it was written until something
    # happened to rewrite it.
    is_overdue = fields.Boolean(compute='_compute_is_overdue')

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for task in self:
            task.is_overdue = bool(
                task.due_date and task.due_date < today and task.state in OPEN_STATES
            )

    @api.constrains('seamstress_id')
    def _check_seamstress(self):
        # A task may be unassigned (it sits in intake until someone picks it up),
        # but it must never be assigned to someone who has left.
        for task in self:
            if task.seamstress_id and not task.seamstress_id.active:
                raise ValidationError(_("That staff member is archived."))

    def action_advance(self, target):
        """Move a task to another state.

        Movement among the OPEN states is now free in both directions. It used
        to be forward-only, and the cost of that landed on the wrong person: a
        seamstress who tapped "Ready" on the row above the one she meant had no
        way back, and the only repair was an owner editing the record in the
        back office. A mis-tap is not history.

        `delivered` is still a one-way door, and deliberately so. It is not a
        label: it stamps delivered_at, releases her to the next job in the
        queue, and is what the shop reads as "the bride has her dress". Undoing
        that is a different act with different consequences, and it is not what
        was asked for.
        """
        self.ensure_one()
        order = [s[0] for s in STATES]
        if target not in order or target == self.state:
            return False
        # Leaving delivered is not a correction, it is a resurrection.
        if self.state == 'delivered':
            return False
        values = {'state': target}
        if target == 'delivered':
            values['delivered_at'] = fields.Datetime.now()
        seamstress = self.seamstress_id
        self.write(values)
        if target == 'delivered' and seamstress:
            # Delivering may have freed her — the queue decides.
            self._modryn_pull_next(seamstress)
        return True

    # --------------------------------------------------------- auto-assignment
    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        # Born assigned — the manager picked her in the finish modal. The
        # auto-assigned path texts through the write hook instead, so the two
        # never double up: _modryn_assign_idle only touches unassigned rows.
        for task in tasks.filtered(lambda t: t.seamstress_id and t.state in OPEN_STATES):
            task._modryn_notify_assigned()
        tasks._modryn_assign_idle()
        return tasks

    def write(self, vals):
        old_assignee = {}
        if 'seamstress_id' in vals:
            old_assignee = {t.id: t.seamstress_id.id for t in self}
        result = super().write(vals)
        if old_assignee:
            for task in self:
                if task.seamstress_id and \
                        task.seamstress_id.id != old_assignee.get(task.id):
                    task._modryn_notify_assigned()
        return result

    def _modryn_notify_assigned(self):
        self.ensure_one()
        notify = self.env['modryn.staff.notify']
        body = self.with_context(
            lang=notify.modryn_lang(self.seamstress_id),
        )._modryn_assignment_body()
        notify.modryn_assigned(self.seamstress_id, body)

    def _modryn_assignment_body(self):
        self.ensure_one()
        parts = [_("New alteration for you: %s") % self.customer_name]
        if self.variant_id:
            parts.append(self.variant_id.product_tmpl_id.name)
        if self.due_date:
            parts.append(_("due %s") % self.due_date.strftime('%d.%m.%Y'))
        return ' — '.join(parts)

    def _modryn_pool(self):
        """Who takes the workshop queue: active employees holding a role the
        owner flagged as workshop — narrowed to today's published rota when one
        exists. modryn_rostered_on answers None both for "no published rota"
        and "published, nobody named", and both waive the gate: a boutique that
        never opens /roster must still get its alterations sewn.
        """
        pool = self.env['hr.employee'].sudo().search([
            # ANY of her roles, not just her first. The seamstress who also
            # sells belongs in the workshop queue on the strength of the
            # seamstress half, and modryn_role_id is a non-stored compute now
            # so it cannot be searched at all.
            ('modryn_role_ids.is_workshop', '=', True),
            ('modryn_level', 'in', ['manager', 'staff']),
        ])
        # Soft registry lookup, not a manifest dependency: the atelier stays
        # installable without the roster, same conditional-coupling style as
        # hr_employee.py's `'modryn_cancelled_at' in Event._fields`.
        if pool and 'modryn.shift.slot' in self.env:
            rostered = self.env['modryn.shift.slot'].modryn_rostered_on(
                datetime.now(TZ).date())
            if rostered is not None:
                pool = pool.filtered(lambda e: e.id in rostered)
        return pool

    def _modryn_open_count(self, employee):
        return self.sudo().search_count([
            ('seamstress_id', '=', employee.id), ('state', 'in', OPEN_STATES)])

    def _modryn_assign_idle(self):
        """Create-side: a newborn unassigned task goes to an idle pool member.

        Idle means ZERO open tasks — 'ready' still counts as open, so only a
        seamstress whose rail is actually empty is handed new work here;
        everyone else queues the task for _modryn_pull_next.
        """
        for task in self:
            if task.seamstress_id or task.state not in OPEN_STATES:
                continue
            idle = task._modryn_pool().filtered(
                lambda e: self._modryn_open_count(e) == 0)
            if not idle:
                continue
            # ponytail: every idle member carries zero load, so "least-loaded
            # idle" degenerates to a deterministic lowest-id pick. Two tasks
            # created in the same instant may also both pick the same member —
            # bounded by human hands at one terminal; the upgrade is FOR UPDATE
            # on the hr_employee row.
            task.seamstress_id = idle.sorted('id')[0]

    def _modryn_pull_next(self, employee):
        """Finish-side: the seamstress who just freed up takes the queue's top.

        Only when she now holds zero open tasks, only when she is in the pool.
        The pick runs FOR UPDATE SKIP LOCKED: two simultaneous finishers each
        lock a DIFFERENT row, so no task is handed out twice — and no unique
        index could referee this race, because a manager assigning by hand may
        legitimately give one seamstress several open tasks.
        """
        if not employee or not employee.active:
            return None
        if employee not in self._modryn_pool():
            return None
        if self._modryn_open_count(employee):
            return None
        self.env.cr.execute("""
            SELECT id FROM modryn_alteration_task
             WHERE seamstress_id IS NULL
               AND state IN ('intake', 'in_progress', 'ready')
             ORDER BY priority DESC, due_date ASC NULLS LAST, id ASC
               FOR UPDATE SKIP LOCKED
             LIMIT 1
        """)
        row = self.env.cr.fetchone()
        if not row:
            return None
        task = self.sudo().browse(row[0])
        task.seamstress_id = employee
        return task

    def _row(self):
        """Plain dict for a template or a JSON route.

        Portal users (managers, seamstresses) have no ORM access to these
        records; everything reaches them through controllers under sudo(), so
        nothing hands out a live recordset.
        """
        self.ensure_one()
        return {
            'id': self.id,
            'customer': self.customer_name,
            'phone': self.customer_phone or '',
            'dress': self.variant_id.product_tmpl_id.name if self.variant_id else '',
            'size': (self.variant_id.product_template_attribute_value_ids[:1].name
                     if self.variant_id else ''),
            'pieces': self.piece_ids.mapped('name'),
            'note': self.note or '',
            'seamstress_id': self.seamstress_id.id or False,
            'seamstress': self.seamstress_id.name or '',
            'state': self.state,
            'priority': self.priority,
            'due': self.due_date.strftime('%d.%m.%Y') if self.due_date else '',
            'overdue': self.is_overdue,
        }
