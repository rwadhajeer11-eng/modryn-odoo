from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

TZ = pytz.timezone('Asia/Jerusalem')


def today():
    """Today in Israel.

    Not fields.Date.today() (that is UTC, so for three hours every evening the
    boutique's "today" and the server's disagree) and not context_today, which
    needs a record this module-level helper does not have.
    """
    return datetime.now(TZ).date()


def week_start(reference=None):
    """The Sunday on or before `reference`. The Israeli week starts Sunday."""
    day = reference or today()
    # Python: Mon=0 … Sun=6, so Sunday is 6 and everything else counts forward.
    return day - timedelta(days=(day.weekday() + 1) % 7)


def next_week_start(reference=None):
    return week_start(reference) + timedelta(days=7)


class ModrynShiftSlot(models.Model):
    """One shift on one real date — Sunday the 16th, morning.

    start_hour/end_hour are SNAPSHOTS of the template, not references to it. A
    manager who moves "Thursday late" an hour later next month must not silently
    rewrite what people already agreed to work, or a published rota changes
    under the staff who signed up for it.
    """

    _name = 'modryn.shift.slot'
    _description = 'A shift on a date'
    _order = 'day, start_hour, id'

    template_id = fields.Many2one(
        'modryn.shift.template', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True)
    day = fields.Date(required=True, index=True)
    start_hour = fields.Float(required=True)
    end_hour = fields.Float(required=True)
    week_start = fields.Date(required=True, index=True)
    published = fields.Boolean(default=False)
    employee_ids = fields.Many2many('hr.employee', string="Working")

    _template_day_uniq = models.Constraint(
        'unique(template_id, day)', "That shift already exists on that date.")

    # ------------------------------------------------------------- generation
    @api.model
    def modryn_ensure_week(self, start=None):
        """Materialise every active template across one week, idempotently.

        Called on demand rather than by a cron: a boutique that never opens the
        roster page does not need rows, and generating on read means a template
        added on Tuesday shows up immediately instead of next scheduler run.
        """
        start = start or next_week_start()
        Template = self.env['modryn.shift.template'].sudo()
        existing = self.sudo().search([('week_start', '=', start)])
        seen = {(s.template_id.id, s.day) for s in existing}

        values = []
        for template in Template.search([]):
            offset = (int(template.weekday) + 1) % 7  # Sunday-first position
            day = start + timedelta(days=offset)
            if (template.id, day) in seen:
                continue
            values.append({
                'template_id': template.id,
                'name': template.name,
                'day': day,
                'start_hour': template.start_hour,
                'end_hour': template.end_hour,
                'week_start': start,
            })
        if values:
            self.sudo().create(values)
        return self.sudo().search([('week_start', '=', start)])

    # ---------------------------------------------------------------- staffing
    def modryn_set_working(self, employee, working):
        """Put someone on this shift, or take her off it."""
        self.ensure_one()
        if working:
            self.sudo().write({'employee_ids': [(4, employee.id)]})
        else:
            self.sudo().write({'employee_ids': [(3, employee.id)]})
        return self

    def modryn_publish(self):
        """Freeze the week. Published shifts stop accepting availability edits."""
        self.sudo().write({'published': True})
        return self

    # ----------------------------------------------------------------- reading
    def _shortages(self):
        """Per-role gaps between what this shift needs and who is on it.

        Counted from the ASSIGNED staff, never from who is available: a shift
        with six volunteers and nobody actually rostered is not covered.
        """
        self.ensure_one()
        assigned = {}
        for employee in self.employee_ids:
            role = employee.modryn_role_id
            if role:
                assigned[role.id] = assigned.get(role.id, 0) + 1
        rows = []
        for target in self.template_id.target_ids:
            have = assigned.get(target.role_id.id, 0)
            rows.append({
                'role': target.role_id.name,
                'required': target.required,
                'have': have,
                'short': max(0, target.required - have),
            })
        return rows

    def _row(self, employee=None):
        self.ensure_one()
        Availability = self.env['modryn.availability'].sudo()
        available = Availability.search([('slot_id', '=', self.id)]).mapped('employee_id')
        shortages = self._shortages()
        mine = None
        if employee:
            mine = bool(Availability.search_count([
                ('slot_id', '=', self.id), ('employee_id', '=', employee.id)]))
        return {
            'id': self.id,
            'name': self.name,
            'day': self.day.strftime('%Y-%m-%d'),
            'day_label': self.day.strftime('%d.%m'),
            'weekday': self.day.strftime('%A'),
            'hours': '%s–%s' % (_fmt(self.start_hour), _fmt(self.end_hour)),
            'published': self.published,
            # Archived staff keep their place on a published shift — the m2o
            # still resolves — but drop out of the pickers for future weeks.
            'available': [{'id': e.id, 'name': e.name, 'role': e.modryn_role_id.name or ''}
                          for e in available if e.active],
            'working': [{'id': e.id, 'name': e.name, 'role': e.modryn_role_id.name or ''}
                        for e in self.employee_ids],
            'shortages': shortages,
            'short_total': sum(s['short'] for s in shortages),
            'i_am_available': mine,
        }


def _fmt(value):
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    return '%02d:%02d' % (hours, minutes)
