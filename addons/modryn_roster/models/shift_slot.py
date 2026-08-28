from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models

from .shift_template import weekday_name
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

    def _shift_type(self):
        """Which part of the day this slot is, read off its template.

        A method and not a stored column on purpose: the slot snapshots its
        HOURS (so editing a template cannot rewrite a week people already agreed
        to) but its part-of-day is the template's classification, and an owner
        fixing a mislabelled evening shift should see that correction on the
        grid rather than only on shifts generated afterwards.

        Deliberately NOT a @property either, and not named `shift_type`: a
        plain attribute that shadows what looks like a field is a trap - the
        ORM would accept slot.shift_type and then blow up on
        slots.mapped('shift_type') or on any search domain naming it, both of
        which read as perfectly ordinary Odoo.
        """
        self.ensure_one()
        return self.template_id.shift_type or 'morning'

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
        """Freeze the week, and tell the women who are on it.

        Publishing used to send NOTHING - no text, no record - so the team
        learned the rota existed only by opening /roster and looking. It is the
        most bell-worthy moment in the product and the only one with no SMS to
        copy, which is exactly why an inventory built from send_async call sites
        would have missed it.

        One notification per woman per week, not per slot: three shifts on a
        published week is one piece of news, and three identical rows in her
        bell is the fastest way to make her stop reading it.
        """
        self.sudo().write({'published': True})

        Notification = self.env['modryn.staff.notification'].sudo()
        actor = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1)
        Notify = self.env['modryn.staff.notify'].sudo()
        for week in set(self.mapped('week_start')):
            slots = self.filtered(lambda s: s.week_start == week)
            for employee in slots.mapped('employee_ids'):
                if not employee.active:
                    continue
                # Composed INSIDE the loop, in HER language - not once in the
                # manager's. _() resolves against the session that happens to be
                # publishing, so a manager working in Hebrew stored a Hebrew
                # sentence in the bell of a woman whose screen is Arabic, and
                # the notification body is plain text that nothing translates
                # later. Every other message in this product already does it
                # this way (booking_comms._localised, day_waitlist, the task
                # escalation); the rota was the one that did not.
                body = self.with_context(
                    lang=Notify.modryn_lang(employee),
                )._modryn_published_body(week)
                Notification.modryn_notify(employee, body, actor=actor)
        return self

    def _modryn_published_body(self, week):
        """The sentence, resolved in whatever language self carries.

        A method and not an inline _() so that with_context(lang=...) is in
        force at the moment the translation is looked up. Calling _() before
        switching context reads the caller's language and no amount of
        with_context afterwards changes the string that already came back.
        """
        return _("The rota for the week of %s is published.") % week.strftime('%d.%m')

    # ----------------------------------------------------------------- reading
    @api.model
    def modryn_rostered_on(self, day):
        """Employee ids the PUBLISHED rota puts on the floor for `day`, or None.

        None and the empty set are different answers and callers must treat them
        so: None means "this day has no published rota", empty means "published,
        nobody on it". Slots are materialised lazily on the first read of
        /roster, so a boutique that never opened the page this week has no rows
        at all — without the None answer the floor board would mark the whole
        team as off-rota on day one.

        A day can carry a morning AND a late shift, so this unions across slots.

        Deliberately does NOT call modryn_ensure_week(): that one WRITES, and
        reading the floor board must never generate a week of shifts.
        """
        slots = self.sudo().search([('day', '=', day), ('published', '=', True)])
        if not slots:
            return None
        rostered = {employee.id for employee in slots.employee_ids}
        # A published day that names NOBODY is not a day the whole team is off —
        # it is another day the rota has nothing to say about. Publishing is
        # week-wide (roster.py publishes every slot sharing a week_start), so a
        # manager who fills Sunday and hits Publish leaves Monday to Thursday
        # published and empty. Returning the empty set there would flag the
        # entire boutique off-rota from Monday, which is exactly the failure the
        # None answer above exists to prevent, one level deeper.
        return rostered or None

    def _shortages(self):
        """Per-role gaps between what this shift needs and who is on it.

        Counted from the ASSIGNED staff, never from who is available: a shift
        with six volunteers and nobody actually rostered is not covered.
        """
        self.ensure_one()
        assigned = {}
        for employee in self.employee_ids:
            # EVERY role she holds, not just the first. modryn_role_id became a
            # non-stored compute over modryn_role_ids when a woman was allowed
            # to hold more than one job, and this counter was missed in that
            # sweep: a seamstress who also sells counted only towards the sales
            # target, so a Thursday with a seamstress rostered on it reported
            # "short one seamstress" and the manager went looking for cover she
            # already had.
            #
            # She counts towards each of them, which slightly OVER-states a
            # shift needing two different jobs at the same instant - one woman
            # cannot cut and sell simultaneously. That is the right way round to
            # be wrong here: this badge answers "is anybody on who can do this
            # job", and the alternative was answering "no" when the answer is
            # plainly yes.
            for role in employee.modryn_role_ids:
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

    def _row(self, employee=None, available_ids=None):
        """One real shift, for the manager's side of the grid.

        `available_ids` is handed in from modryn_week_map rather than looked up
        here. It used to run a search AND a search_count per slot - fifteen
        queries to draw five cards, and it would have been sixty-three for
        twenty-one. Nothing in the gate asserts a query count, so the only
        symptom of leaving it alone would have been a page that quietly got
        four times slower.

        Availability is now keyed on (day, shift_type, employee) and no longer
        points at a slot at all, so two templates that legitimately share one
        weekday-and-part both resolve to the SAME offer list - which is correct:
        "I can work Sunday morning" qualifies her for either of them.
        """
        self.ensure_one()
        if available_ids is None:
            available_ids = self.env['modryn.availability'].sudo().modryn_week_map(
                self.week_start).get((self.day, self._shift_type()), [])
        available = self.env['hr.employee'].sudo().browse(available_ids).exists()
        shortages = self._shortages()
        mine = bool(employee and employee.id in available_ids)
        return {
            'id': self.id,
            'name': self.name,
            'day': self.day.strftime('%Y-%m-%d'),
            'day_label': self.day.strftime('%d.%m'),
            'weekday': weekday_name(self.day),
            'hours': '%s–%s' % (_fmt(self.start_hour), _fmt(self.end_hour)),
            # Which part of the day this is, so the page can lay the week out as
            # a 7x3 grid without asking the template model a second time per slot.
            'shift_type': self._shift_type(),
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
