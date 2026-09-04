"""The manager's own screen.

A hub rather than a page about one thing: the first block on it is the one the
owner asked for - say something to the team - and the rest of what a manager
does all day already has screens, so this is where the things that do not fit
one of those will land.

Sending is MANAGER-level, not merely page-level. The role matrix decides what a
role can SEE and levels decide what it can DO, which is the rule every other
controller here follows; an announcement reaches every phone in the shop, and
that is not a thing a page grant should be able to hand out.
"""

import calendar
from datetime import date, datetime, timedelta

import pytz
import werkzeug.urls

from odoo import _, fields, http
from odoo.exceptions import ValidationError
from odoo.http import content_disposition, request
from odoo.tools.misc import format_date
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_booking.models.opening_hours import (
    weekday_selection as _hours_weekday_selection)
from odoo.addons.modryn_booking.models.queue_hours import (
    DEFAULT_PER_HOUR as QUEUE_DEFAULT)

from .. import nav


def _hours_weekdays():
    """Sunday first, the same order the opening-hours screen uses.

    Called rather than stored: the labels are translated at read time, and a
    module-level list would freeze whichever language happened to import it.
    """
    return _hours_weekday_selection(None)
from . import access
from .manage import ModrynManage, _file_papers, _levels, _personal_from

_lt = LazyTranslate(__name__)

TZ = pytz.timezone('Asia/Jerusalem')

# How many past announcements the screen shows. Enough to see what the team was
# told this week and to unsend the one that was wrong; a manager who wants the
# whole history of a boutique is asking a different question than this screen.
RECENT = 20


class ModrynManagerScreen(http.Controller):

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _team(self, archived=False):
        """Everyone who could be told something, in a stable order.

        active_test=False and an explicit `active` term, rather than letting the
        default do it: the announcement picker and the team box read this, and
        a silent default is how the archived list came to be unreachable in the
        first place.
        """
        return request.env['hr.employee'].sudo().with_context(
            active_test=False).search([
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
                ('active', '=', not archived),
            ], order='name')

    def _papers(self, employee):
        """What the boutique holds for one woman.

        The same query her own profile runs, against the same attachments: this
        screen and that one are two doors onto one folder, not two folders that
        have to be kept in step.
        """
        return request.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.employee'),
            ('res_id', '=', employee.id),
            ('name', '!=', False),
        ], order='create_date desc')

    def _team_rows(self, archived=False):
        """Everything the Team screen shows, plus the papers.

        Built from that screen's OWN row builder rather than a second query
        assembling the same fields: one function and two readers cannot start
        disagreeing about what a woman's phone number is, and a copy would have
        quietly missed the next field somebody adds.
        """
        rows = ModrynManage()._employee_rows()
        by_id = {employee.id: employee for employee in self._team(archived)}
        out = []
        for row in rows:
            employee = by_id.get(row['id'])
            if not employee:
                # Not in the half being asked for. Her papers do not vanish
                # either way - they stay against her record and come back with
                # her if she is restored.
                continue
            out.append(dict(row, papers=[{
                'id': doc.id,
                'name': doc.name,
                'at': doc.create_date.strftime('%d.%m.%Y')
                      if doc.create_date else '',
            } for doc in self._papers(employee)],
                changes=self._changes(employee)))
        return out

    # How many edits a card carries. Five, because this is a "has anything moved
    # lately" glance and not the archive - the audit page is the archive, and it
    # is linked from here rather than reproduced.
    CHANGES_SHOWN = 5

    def _changes(self, employee):
        """Her own details, as they were changed.

        Guarded on the model EXISTING, not assumed: the audit log lives in
        modryn_ops and this module is underneath it, so a boutique running the
        staff module without the operations one gets a card with no history
        rather than a traceback. The same `in env` guard the outcome fields get.
        """
        if 'modryn.audit.log' not in request.env:
            return []
        return [dict(row, id=row['id']) for row in request.env[
            'modryn.audit.log'].sudo().search([
                ('model', '=', 'hr.employee'),
                ('res_id', '=', employee.id),
            ], limit=self.CHANGES_SHOWN)._row_list()]

    def _recent(self):
        Announcement = request.env['modryn.announcement'].sudo()
        rows = []
        for record in Announcement.search([], limit=RECENT):
            people = record.employee_ids
            rows.append({
                'id': record.id,
                'body': record.body or '',
                'author': record.author_name or '',
                # Whom it went to, as names rather than a count: "everyone" and
                # "these four" are different facts and a manager unsending
                # something needs to see which one she sent.
                'to': ', '.join(people.mapped('name')),
                'everyone': not people,
                'at': pytz.utc.localize(record.create_date).astimezone(TZ)
                          .strftime('%d.%m %H:%M') if record.create_date else '',
            })
        return rows

    # The blocks this screen is made of, in the order they are offered. A list
    # rather than a template full of hard-coded tiles: adding the next one is a
    # line here, and the tiles and the routing cannot disagree about what
    # exists.
    # 'hours' is the boutique's OPENING hours; 'worked' is who stood in it.
    # Two different questions that both want the word - named apart here so a
    # link can never quietly open the other one.
    VIEWS = ('announce', 'team', 'hours', 'rooms', 'worked', 'sales',
             'track', 'shop', 'codes', 'queue')

    def _account_form(self, editing=None, adding=False, errors=None, values=None):
        """The add/edit form's context, or nothing at all.

        Returned empty unless the box is actually showing the form, so a manager
        reading the cards never has a roles list and a levels list built for a
        form she is not going to be shown.
        """
        if not (adding or editing):
            return {}
        employee = None
        if editing:
            employee = request.env['hr.employee'].sudo().with_context(
                active_test=False).browse(editing).exists()
            if not employee:
                return {}
        if values is None:
            # Her stored answers, or an empty form. The same shape
            # staff_edit_form built, so the lifted markup reads it unchanged.
            values = dict(
                _personal_from(employee),
                name=employee.name,
                phone=employee.work_phone or '',
                role_ids=employee.modryn_role_ids.ids,
                level=employee.modryn_level,
            ) if employee else {}
        return {
            'form_open': True,
            'roles': ModrynManage()._roles(),
            'levels': _levels(),
            'employee': employee,
            'errors': errors or {},
            'values': values,
            # Only when she EXISTS. A woman being added has no folder yet and
            # nowhere to hang one: the file field on the add form travels with
            # the same Save that creates her.
            'form_papers': [{
                'id': doc.id,
                'name': doc.name,
                'at': doc.create_date.strftime('%d.%m.%Y'),
            } for doc in self._papers(employee)] if employee else [],
        }

    def _worked_context(self, whose=None, month=None, error=None):
        """The team to choose from, and one woman's month if one is chosen.

        The month itself comes from ModrynStaffAuth._hours_context - the same
        function her own profile calls, against the same model method. A second
        implementation here would be a second answer to "how many hours in
        March", and the two would drift on the first change to either.
        """
        from .auth import ModrynStaffAuth

        def row(e):
            return {'id': e.id, 'name': e.name,
                    'chosen': bool(whose) and e.id == whose.id}

        context = {
            'worked_team': [row(e) for e in self._team()],
            # The women who have LEFT, offered separately. Their hours are what
            # a last pay packet is worked out from, so leaving them out of this
            # picker makes the one month somebody actually needs the only one
            # they cannot reach. Grouped rather than mixed in, because "who is
            # on this month" is the question asked nine times in ten.
            'worked_gone': [row(e) for e in self._team(archived=True)],
            'worked_who': whose,
            'worked_error': error,
        }
        if whose:
            hours = ModrynStaffAuth()._hours_context(whose, month)
            context.update(hours)
            # The month actually being shown, which is not always the month
            # asked for: _hours_context falls back to her most recent one when
            # the request names a month she has nothing in. Computed here, once,
            # rather than by a list comprehension inside three t-att-values -
            # a form that posts back a DIFFERENT month than the page is showing
            # sends the manager somewhere she did not come from.
            context['chosen_month'] = next(
                (m['key'] for m in hours['months'] if m['chosen']), '')
        return context

    # Two characters, the same floor the dress picker and the customer lookup
    # use. One letter matches most of the shop and answers nothing.
    SALES_MIN_QUERY = 2

    # How far back the page looks. Sixty days, because the question it answers
    # is "what has been going on lately" and not "what has ever happened" - the
    # audit log is the archive, and it is linked from here rather than copied.
    TRACK_DAYS = 60
    TRACK_LIMIT = 120

    def _tracked(self):
        """What happened lately that the owner would want to know about.

        Merged and sorted here rather than in the template: two models with two
        shapes have to become one column of events before anybody can read them
        in order, and a template that sorted would be a template with a query
        in it.
        """
        since = fields.Datetime.now() - timedelta(days=self.TRACK_DAYS)
        rows = []

        # Money off a price. The reason is not optional at the counter, which
        # is what makes this list worth opening.
        if 'modryn.sale' in request.env:
            for sale in request.env['modryn.sale'].sudo().search([
                    ('sold_at', '>=', fields.Datetime.to_string(since)),
                    ('discount_amount', '>', 0),
            ], order='sold_at desc', limit=self.TRACK_LIMIT):
                rows.append({
                    'kind': 'discount',
                    'id': 'd%s' % sale.id,
                    'when': sale.sold_at,
                    'who': sale.employee_id.name or '',
                    'about': sale.customer_name or '',
                    'what': ' · '.join(sale.line_ids.mapped('description')),
                    'why': sale.discount_reason or '',
                    'from_amount': round(sale.subtotal or 0.0),
                    'to_amount': round(sale.total or 0.0),
                    'size': sale.modryn_discount_sentence(),
                })

        # A call for help. Who called, who she called, and what for - the three
        # things a shout across the floor does not leave behind.
        if 'modryn.sos.call' in request.env:
            Call = request.env['modryn.sos.call'].sudo()
            # Off the FIELD, not off the selection list on the model: that list
            # is plain Python and is the English the file was written in. Read
            # once, not once per call.
            states = dict(Call.fields_get(['state'])['state']['selection'])
            for call in Call.search([
                    ('create_date', '>=', fields.Datetime.to_string(since)),
            ], order='create_date desc', limit=self.TRACK_LIMIT):
                rows.append({
                    'kind': 'sos',
                    'id': 's%s' % call.id,
                    'when': call.create_date,
                    'who': call.caller_id.name or '',
                    # Empty means the general bell rather than one colleague,
                    # and the screen says so rather than leaving a gap.
                    'target': call.target_id.name or '',
                    'about': call._customer_name(),
                    'why': call.note or '',
                    'state': states.get(call.state, call.state),
                    'answered_by': call.acked_by_id.name or '',
                    # The whole sentence, with the name inside it. Split around
                    # a t-out it exported as the bare word "Called", which is
                    # not a sentence in any language and least of all in one
                    # where the name comes first.
                    'called': (
                        _("Called %(who)s") % {'who': call.target_id.name}
                        if call.target_id else _("Called whoever was free")),
                    'resolved': call.state == 'resolved',
                })

        rows.sort(key=lambda r: (r['when'] is not None, r['when']), reverse=True)
        return rows[:self.TRACK_LIMIT]

    def _sales(self, query):
        """Everything sold to whoever matches `query`, newest first.

        Read live rather than kept in a summary table: a sale is written once
        and never changes, so there is nothing to keep in step, and a stored
        rollup would be a staleness bug waiting for a backfill. The same
        reasoning the reports page already applies to its own figures.
        """
        query = (query or '').strip()
        if len(query) < self.SALES_MIN_QUERY:
            return []
        like = '%%%s%%' % query
        rows = []

        # The appointments. modryn_outcome lives in modryn_ops, so a boutique
        # without the catalogue module has no sales history rather than a
        # traceback - the same `in _fields` guard booking uses for cancellations.
        Event = request.env['calendar.event']
        if 'modryn_outcome' in Event._fields:
            for event in Event.sudo().search([
                    ('modryn_is_booking', '=', True),
                    ('modryn_outcome', '=', 'sold'),
                    '|', ('name', 'ilike', like),
                         ('modryn_customer_phone', 'ilike', like),
            ], order='modryn_outcome_at desc', limit=60):
                rows.append({
                    'kind': 'booking',
                    'id': event.id,
                    'name': event.name or '',
                    'phone': event.modryn_customer_phone or '',
                    'dress': event.modryn_sale_items or '',
                    'amount': round(event.modryn_sale_amount or 0.0),
                    # What she actually paid, typed by the stylist who closed it.
                    'amount_is_catalogue': False,
                    'when': event.modryn_outcome_at,
                    'by': event.modryn_outcome_by_id.name or '',
                })

        # The walk-ins. No typed price on this side - the floor records WHICH
        # dress left, so the catalogue price is the best available answer and is
        # labelled as such rather than passed off as the till.
        Entry = request.env['modryn.queue.entry']
        if 'modryn_outcome' in Entry._fields:
            for entry in Entry.sudo().search([
                    ('modryn_outcome', '=', 'sold'),
                    '|', ('name', 'ilike', like), ('phone', 'ilike', like),
            ], order='modryn_outcome_at desc', limit=60):
                variant = entry.modryn_variant_id
                size = variant.product_template_attribute_value_ids[:1].name
                rows.append({
                    'kind': 'walkin',
                    'id': entry.id,
                    'name': entry.name or '',
                    'phone': entry.phone or '',
                    'dress': '%s%s' % (
                        variant.product_tmpl_id.name or '',
                        ' · %s' % size if size else '') if variant else '',
                    'amount': round(variant.list_price or 0.0) if variant else 0,
                    'amount_is_catalogue': True,
                    'when': entry.modryn_outcome_at,
                    'by': entry.modryn_employee_id.name or '',
                })

        # The till. Its own model, with lines and a discount, so what it
        # contributes here is richer than the other two: the items are named
        # individually and the price carries the reason it is what it is.
        Sale = request.env['modryn.sale'] if 'modryn.sale' in request.env             else None
        if Sale is not None:
            for sale in Sale.sudo().search([
                    '|', ('customer_name', 'ilike', like),
                         ('customer_phone', 'ilike', like),
            ], order='sold_at desc', limit=60):
                rows.append({
                    'kind': 'till',
                    'id': sale.id,
                    'name': sale.customer_name or '',
                    'phone': sale.customer_phone or '',
                    'dress': ' · '.join(sale.line_ids.mapped('description')),
                    'amount': round(sale.total or 0.0),
                    # A till sale IS what she handed over - the one of the
                    # three sources where the figure needs no apology.
                    'amount_is_catalogue': False,
                    'when': sale.sold_at,
                    'by': sale.employee_id.name or '',
                    # What came off, and why. Empty when nothing did, so the
                    # card shows the block only when there is something to say.
                    'discount': sale.modryn_discount_sentence(),
                    'discount_amount': round(sale.discount_amount or 0.0),
                    'before': round(sale.subtotal or 0.0),
                    'discount_reason': sale.discount_reason or '',
                    # The till records its own alteration, which is the one the
                    # bride was told about at the counter. It is shown beside
                    # the workshop's tasks rather than instead of them.
                    'altered_here': sale.alteration_note or '',
                    'altered_by': sale.alteration_by_id.name or '',
                })

        # Newest first across BOTH kinds, which a single query could not do.
        # A sale with no recorded moment sorts last rather than crashing the
        # comparison: those are old rows from before the timestamp existed.
        rows.sort(key=lambda r: (r['when'] is not None, r['when']), reverse=True)

        # And what the workshop did to it. Matched on PHONE - a name is spelled
        # three ways by three people and a number is a number - so a bride who
        # bought and then had it taken in reads as one story on one row.
        Task = request.env['modryn.alteration.task'].sudo()
        # The state's label in HER language. The model's own helper, because
        # reading _fields[...].selection hands back the module-level list, which
        # is plain Python and therefore English - it printed "Intake" on a
        # Hebrew page. Looked up once, not once per task.
        states = dict(Task.modryn_selection('state')) if hasattr(
            Task, 'modryn_selection') else {}
        for row in rows:
            row['alterations'] = [{
                'what': task.note or '',
                'pieces': ', '.join(task.piece_ids.mapped('name')),
                'taken_in': task.create_date,
                'finished': task.delivered_at,
                'state': states.get(task.state, task.state),
                'done': task.state == 'delivered',
            } for task in Task.search(
                self._same_person(row), order='create_date desc')]
        return rows

    @staticmethod
    def _same_person(row):
        """Her alteration work, by number OR by name.

        Phone first, because it is this product's contact identity - but not
        phone ONLY. The workshop takes a garment in at the counter and the
        number typed there is whoever answered: a mother's, a second phone, or
        nothing at all. Matching on phone alone lost every one of those, and the
        bride reads as somebody who bought a dress and never had it touched.

        The cost of the wider match is two brides who share a name being shown
        each other's alterations. Against losing the connection entirely, on a
        screen the owner reads rather than one that acts on the answer, that is
        the better way to be wrong - and the number is on the card, so she can
        see which is which.
        """
        terms = []
        if row.get('phone'):
            terms.append(('customer_phone', '=', row['phone']))
        if row.get('name'):
            terms.append(('customer_name', '=', row['name']))
        if not terms:
            # Never an empty domain: that would hand back every alteration the
            # boutique has ever done and hang it on one bride.
            return [('id', '=', 0)]
        return (['|'] * (len(terms) - 1)) + terms

    def _render(self, error=None, draft=None, picked=None, file_error=None,
                file_for=None, view=None, editing=None, adding=False,
                form_errors=None, form_values=None, archived=False, whose=None):
        context = {
            # None means the tiles themselves. Anything unrecognised falls back
            # to them rather than 404ing: a stale link should land somewhere
            # useful, not on an error.
            'view': view if view in self.VIEWS else None,
            'team': [{'id': e.id, 'name': e.name} for e in self._team()],
            # One half or the other. The announcement picker above stays on
            # the ACTIVE list whatever this says: telling a woman who left in
            # March about Thursday's delivery is not a thing to offer.
            'team_rows': self._team_rows(archived),
            'showing_archived': archived,
            # Adding a person, setting a password, changing a level: the owner's
            # alone. The cards carry the links for her so she loses nothing by
            # the move, and a manager simply does not see them.
            'is_owner': access.is_owner(),
            'announcements': self._recent(),
            'error': error,
            # Which worker's upload went wrong, so the sentence appears against
            # HER card rather than at the top of a screen listing eight people.
            'file_error': file_error,
            'file_for': file_for,
            # What she typed, handed back so a refusal never costs her the
            # message she had written.
            'draft': draft or '',
            'picked': picked or [],
            'active_tab': 'boss',
        }
        # The owner's panels, and only when she is going to see them: building a
        # closures list, or a roles list, for a manager who will never be shown
        # either is work for nobody.
        if view == 'track':
            context['tracked'] = self._tracked()
            context['track_intro'] = _(
                "The last %(days)s days. Money off a price, and calls for help."
            ) % {'days': self.TRACK_DAYS}
        if view == 'sales':
            # The whole history, searched. Nothing is drawn until she has typed
            # two characters: a page that opens on every sale the boutique has
            # ever made is a page nobody can read, and the question here is
            # always about one bride.
            q = request.params.get('q') or ''
            context['sales_query'] = q
            context['sales'] = self._sales(q)
            context['sales_min'] = self.SALES_MIN_QUERY
        if view == 'worked':
            # The MANAGER's, deliberately, unlike the four panels below. She is
            # the one standing there when somebody goes home without pressing
            # the button; making her find the owner to correct it is how the
            # figure stays wrong.
            context.update(self._worked_context(
                whose=whose, month=request.params.get('month'),
                error=request.params.get('error')))
        if view == 'queue':
            context.update(self._queue_hours_context(
                error=request.params.get('error'),
                confirm=request.params.get('confirm')))
            context.update(self._queue_month_context(
                month=request.params.get('month'),
                day=request.params.get('day')))
        if view == 'codes':
            # THE MANAGER'S, not the owner's. She is the one who decides that
            # this week's fair gets ten percent, and making her find the owner
            # to type it is how a code stops being used at all.
            Code = request.env['modryn.discount.code'].sudo().with_context(
                active_test=False)
            context['codes'] = Code.search([])
            context['codes_error'] = request.params.get('error') or ''
        if access.is_owner():
            if view == 'hours':
                context.update(ModrynManage().hours_context(
                    error=request.params.get('error')))
            elif view == 'rooms':
                context.update(ModrynManage().rooms_context(
                    error=request.params.get('error'),
                    confirm=request.params.get('confirm')))
            elif view == 'shop':
                context.update(self._shop_details_context(
                    error=request.params.get('error')))
            elif view == 'team':
                context.update(self._account_form(
                    editing=editing, adding=adding,
                    errors=form_errors, values=form_values))
        return request.render('modryn_staff.manager_screen', context)

    # ------------------------------------------------- the shop's own details
    @staticmethod
    def _shop_details_context(error=None):
        """What a customer is told about this boutique.

        THE SITE ALREADY READS ALL OF THIS - the header, the footer and the
        contact page have read res.company since the theme was written - and
        there has never been anywhere to type it. Every boutique therefore
        showed Odoo's placeholders: an address in America, a phone nobody
        answers, info@yourcompany.example.com. This is the missing half.
        """
        company = request.env.company.sudo()
        return {
            'shop_error': error or '',
            'shop': {
                'name': company.name or '',
                'phone': company.phone or '',
                'whatsapp': company.modryn_whatsapp or '',
                'email': company.email or '',
                'street': company.street or '',
                'city': company.city or '',
                # Shown back so she can see what the button will actually open,
                # rather than trusting that her 052- turned into something
                # WhatsApp accepts.
                'whatsapp_number': company.modryn_whatsapp_number(),
            },
        }

    # --------------------------------------------------------- booking hours
    # A YEAR, and not further. Long enough for next season's fairs and every
    # holiday, short enough that the month walker cannot wander into 2071 and
    # leave her pressing "back" forty times.
    QUEUE_MONTHS_AHEAD = 12

    @staticmethod
    def _queue_month_context(month=None, day=None):
        """The month she is looking at, and the one date she opened.

        Built from the calendar module rather than by counting days: the first
        cell of the grid has to be the Sunday on or before the 1st, and the
        arithmetic for that is exactly the arithmetic that is wrong by one when
        it is written by hand in a template.
        """
        today = fields.Date.context_today(request.env['modryn.queue.day'])
        first_allowed = today.replace(day=1)
        # date(y, m, 1) walked forward QUEUE_MONTHS_AHEAD months, without
        # depending on dateutil — the addon does not have it as a dependency
        # and one line of modulo is cheaper than gaining one.
        total = first_allowed.year * 12 + (first_allowed.month - 1) \
            + ModrynManagerScreen.QUEUE_MONTHS_AHEAD
        last_allowed = date(total // 12, total % 12 + 1, 1)

        shown = first_allowed
        if month:
            try:
                year_s, month_s = str(month).split('-')[:2]
                shown = date(int(year_s), int(month_s), 1)
            except (TypeError, ValueError):
                shown = first_allowed
        # Clamped rather than refused: a hand-typed month outside the year is a
        # mistyped URL, and the nearest real month is a better answer than a
        # 404 on a screen she reached by pressing an arrow.
        shown = min(max(shown, first_allowed), last_allowed)

        def step(base, delta):
            n = base.year * 12 + (base.month - 1) + delta
            return date(n // 12, n % 12 + 1, 1)

        previous = step(shown, -1)
        following = step(shown, 1)
        last_day = date(shown.year, shown.month,
                         calendar.monthrange(shown.year, shown.month)[1])

        Day = request.env['modryn.queue.day'].sudo()
        named = Day.modryn_days(shown, last_day)
        Hours = request.env['modryn.opening.hours']
        pattern = Hours.modryn_hours_by_weekday()

        # The grid starts on the Sunday on or before the 1st and runs whole
        # weeks, because that is what a month looks like on a wall.
        start = shown - timedelta(days=(shown.weekday() + 1) % 7)
        weeks, row = [], []
        cursor = start
        while cursor <= last_day or len(row) % 7:
            hours = named.get(cursor)
            from_pattern = hours is None
            if from_pattern:
                hours = pattern.get(str(cursor.weekday()), {})
            seats = sum(count for count in hours.values() if count > 0)
            row.append({
                'date': cursor,
                'key': cursor.strftime('%Y-%m-%d'),
                'number': cursor.day,
                'this_month': cursor.month == shown.month,
                'past': cursor < today,
                'today': cursor == today,
                # Where the answer came from, and it is the only thing on this
                # screen a manager cannot work out by looking: two identical
                # cells, one of which will follow a change to the week and one
                # of which will not.
                'own': not from_pattern,
                'seats': seats,
                'open_hours': len([c for c in hours.values() if c > 0]),
            })
            if len(row) == 7:
                weeks.append(row)
                row = []
            cursor += timedelta(days=1)
        if row:
            weeks.append(row)

        chosen = None
        if day:
            try:
                year_s, month_s, day_s = str(day).split('-')[:3]
                picked = date(int(year_s), int(month_s), int(day_s))
            except (TypeError, ValueError):
                picked = None
            if picked and first_allowed <= picked <= last_allowed:
                own = Day.modryn_on(picked)
                fallback = pattern.get(str(picked.weekday()), {})
                source = own or fallback
                chosen = {
                    'key': picked.strftime('%Y-%m-%d'),
                    'label': picked.strftime('%d.%m.%Y'),
                    'own': bool(own),
                    'hours': [{
                        'hour': float(hour),
                        'text': '%02d:00' % hour,
                        'how_many': source.get(float(hour), 0),
                    } for hour in range(24)],
                }
        return {
            'queue_weeks': weeks,
            'queue_month': shown.strftime('%Y-%m'),
            'queue_month_label': '%s %s' % (
                format_date(request.env, shown, date_format='LLLL'), shown.year),
            'queue_prev': previous.strftime('%Y-%m')
                          if previous >= first_allowed else None,
            'queue_next': following.strftime('%Y-%m')
                          if following <= last_allowed else None,
            'queue_weekday_heads': [label for _code, label in _hours_weekdays()],
            'queue_day': chosen,
        }

    @staticmethod
    def _queue_hours_context(error=None, confirm=None):
        """The whole week, every hour of it, and the kinds she accepts.

        SEVEN DAYS AND TWENTY-FOUR HOURS, not the hours the door happens to be
        open. It drew the opening hours first and that was wrong twice over: it
        showed five days because five days had windows, so a shop that works
        Friday had nowhere to say so, and it stopped at six because the windows
        did, so a shop open until ten at night could not offer nine.

        PREFILLED FROM THE WEEK SHE ALREADY HAS. A boutique that has never
        opened this screen sees its opening hours filled in with one each and
        everything else at nothing - which is exactly what its website is doing
        today. Her first save writes all of it down, and from then on this grid
        is the whole answer.
        """
        Hours = request.env['modryn.opening.hours']
        Queue = request.env['modryn.queue.hour'].sudo()
        said = Queue.modryn_grid()
        # Only when she has never spoken: after that, a blank cell means none
        # and must not be quietly refilled from the door.
        fallback = {} if said else Hours.modryn_open_hours_by_weekday()

        days = []
        for code, label in _hours_weekdays():
            open_today = set(fallback.get(code) or [])
            days.append({
                'code': code,
                'label': label,
                'hours': [{
                    'hour': float(hour),
                    'text': '%02d:00' % hour,
                    'how_many': said.get(code, {}).get(
                        float(hour),
                        QUEUE_DEFAULT if float(hour) in open_today else 0),
                } for hour in range(24)],
            })
        kinds = request.env['modryn.customer.kind'].sudo().with_context(
            active_test=False).search([])
        Booking = request.env['calendar.event'].sudo()
        confirming = kinds.browse()
        if confirm and str(confirm).isdigit():
            confirming = kinds.filtered(lambda k: k.id == int(confirm))
        return {
            'queue_error': error or '',
            'queue_days': days,
            # The hours, once, for the table's left-hand column.
            'queue_clock': ['%02d:00' % hour for hour in range(24)],
            'queue_kinds': kinds,
            # Bookings per kind. The screen offers Delete only where deleting is
            # possible; everywhere else it says why, which is better than a
            # button that always refuses.
            'queue_kind_booked': {
                kind.id: Booking.search_count(
                    [('modryn_customer_kind_id', '=', kind.id)])
                for kind in kinds},
            'queue_kind_confirming': confirming,
        }

    @http.route('/manage/queue-hours', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def queue_hours_save(self, **post):
        """The whole grid at once.

        REPLACE-SET, not a row at a time: the form posts every hour it drew, so
        what is stored is exactly what she is looking at. Saving one cell at a
        time would mean a page load per hour, and a week has fifty of them.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        form = request.httprequest.form
        Queue = request.env['modryn.queue.hour'].sudo()
        wanted = {}
        for key in form.keys():
            # hour_<weekday>_<hour with the dot as an underscore>
            if not key.startswith('hour_'):
                continue
            _, weekday, raw_hour = key.split('_', 2)
            try:
                hour = float(raw_hour.replace('-', '.'))
                how_many = int(form.get(key) or 0)
            except (TypeError, ValueError):
                continue
            wanted[(weekday, round(hour, 4))] = max(how_many, 0)

        if not wanted:
            return request.redirect('/manage/team-screen?view=queue')

        existing = {(row.weekday, round(row.hour, 4)): row
                    for row in Queue.search([])}
        for key, how_many in wanted.items():
            row = existing.get(key)
            if row:
                row.how_many = how_many
            else:
                Queue.create({'weekday': key[0], 'hour': key[1],
                              'how_many': how_many})
        return request.redirect('/manage/team-screen?view=queue')

    @http.route('/manage/queue-day', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def queue_day_save(self, **post):
        """One date's own hours, replacing whatever it had.

        REPLACE-SET, exactly like the weekly grid next to it: the form posts
        all twenty-four hours, so what is in the boxes when Save lands is what
        that date is. Anything else and an hour cleared to nothing would keep
        its old number, which is the bug the weekly grid was written to avoid.

        A date is stored even when every hour is zero. "Open, and the website
        gives nothing away" is a real answer and the ONLY way to say it — a
        date with no rows at all falls back to the weekly pattern, which is a
        different sentence entirely.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        raw = (post.get('day') or '').strip()
        try:
            day = datetime.strptime(raw, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return request.redirect('/manage/team-screen?view=queue')

        Day = request.env['modryn.queue.day'].sudo()
        form = request.httprequest.form
        wanted = {}
        for key in form.keys():
            # hour_<hour with the dot as a dash>, the same shape the weekly
            # grid posts, minus the weekday it does not need.
            if not key.startswith('hour_'):
                continue
            try:
                hour = float(key[5:].replace('-', '.'))
                how_many = int(form.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= hour < 24:
                wanted[round(hour, 4)] = max(how_many, 0)
        if not wanted:
            return request.redirect('/manage/team-screen?view=queue')

        existing = {round(row.hour, 4): row for row in Day.search([('day', '=', day)])}
        for hour, how_many in wanted.items():
            row = existing.get(hour)
            if row:
                row.how_many = how_many
            else:
                Day.create({'day': day, 'hour': hour, 'how_many': how_many})
        return request.redirect(
            '/manage/team-screen?view=queue&month=%s&day=%s'
            % (day.strftime('%Y-%m'), day.strftime('%Y-%m-%d')))

    @http.route('/manage/queue-day/clear', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def queue_day_clear(self, **post):
        """Give a date back to the weekly pattern.

        Not the same as setting every hour to zero, and the screen says so:
        zero everywhere means the shop is open and offering nothing online,
        while clearing means the date has no opinion of its own and follows the
        week again — including any later change to the week.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        raw = (post.get('day') or '').strip()
        try:
            day = datetime.strptime(raw, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return request.redirect('/manage/team-screen?view=queue')
        request.env['modryn.queue.day'].sudo().search(
            [('day', '=', day)]).unlink()
        return request.redirect(
            '/manage/team-screen?view=queue&month=%s&day=%s'
            % (day.strftime('%Y-%m'), day.strftime('%Y-%m-%d')))

    @http.route('/manage/queue-kind/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def queue_kind_new(self, **post):
        """Another kind of visitor who may ask for an appointment."""
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/team-screen?view=queue&error=%s'
                                    % werkzeug.urls.url_quote(
                                        _("Give the kind of visitor a name.")))
        try:
            with request.env.cr.savepoint():
                request.env['modryn.customer.kind'].sudo().create({
                    'name': name,
                    'note': (post.get('note') or '').strip(),
                })
        except ValidationError as err:
            return request.redirect(
                '/manage/team-screen?view=queue&error=%s'
                % werkzeug.urls.url_quote(
                    err.args[0] if err.args
                    else _("That kind of visitor already exists.")))
        return request.redirect('/manage/team-screen?view=queue')

    @http.route('/manage/queue-kind/archive/<int:kind_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def queue_kind_archive(self, kind_id, **post):
        """Stop offering a kind, or offer it again.

        Archived and never deleted: appointments point at it, and a kind
        removed from the table would leave those bookings saying nothing about
        who turned up.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        kind = request.env['modryn.customer.kind'].sudo().with_context(
            active_test=False).browse(kind_id).exists()
        if kind:
            kind.active = not kind.active
        return request.redirect('/manage/team-screen?view=queue')

    @http.route('/manage/queue-kind/delete/<int:kind_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def queue_kind_delete(self, kind_id, **post):
        """Gone for good — the door for a line typed by mistake.

        Refused BY NAME once a booking points at the kind, and the field is
        ondelete='restrict' underneath, so the refusal holds even if this check
        is ever bypassed. A bride who said she was a bride must keep saying it:
        deleting the kind would leave her appointment answering "who is coming"
        with nothing, months after she came.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        kind = request.env['modryn.customer.kind'].sudo().with_context(
            active_test=False).browse(kind_id).exists()
        if not kind:
            return request.redirect('/manage/team-screen?view=queue')
        booked = request.env['calendar.event'].sudo().search_count(
            [('modryn_customer_kind_id', '=', kind.id)])
        if booked:
            return request.redirect(
                '/manage/team-screen?view=queue&error=%s'
                % werkzeug.urls.url_quote(_(
                    "%(kind)s cannot be deleted — %(count)s appointments say "
                    "that is who is coming. Stop offering it instead.",
                    kind=kind.name, count=booked)))
        name = kind.name
        kind.unlink()
        return request.redirect(
            '/manage/team-screen?view=queue&error=%s'
            % werkzeug.urls.url_quote(_("%s was deleted.", name)))

    # ------------------------------------------------------- discount codes
    @http.route('/manage/codes/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def code_new(self, **post):
        """A word, and what it takes off.

        MANAGER AND OWNER BOTH: this is the screen named after the manager and
        the decision is hers to make.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()

        def refuse(message):
            return request.redirect('/manage/team-screen?view=codes&error=%s'
                                    % werkzeug.urls.url_quote(message))

        code = (post.get('code') or '').strip()
        if not code:
            return refuse(_("The code needs a word."))

        # WHAT COMES OFF. Validated against the two the field actually has, not
        # trusted: an unrecognised kind here would store a code that takes
        # nothing off and looks exactly like one that works.
        kind = post.get('value_kind') if post.get('value_kind') in ('percent', 'amount') \
            else 'percent'
        try:
            value = float(post.get('value') or 0)
        except (TypeError, ValueError):
            value = 0.0
        if kind == 'percent' and not 0 < value <= 100:
            return refuse(_("A discount is between 1 and 100 percent."))
        if kind == 'amount' and value <= 0:
            return refuse(_("Say how many shekels come off."))

        # WHEN AND FOR HOW MANY. Three boxes, each of which may be left empty,
        # and empty means no limit. Nothing here picks between them — a code
        # can carry a week and a headcount at the same time, which is the
        # sentence a boutique actually says.
        try:
            max_uses = int(post.get('max_uses') or 0)
        except (TypeError, ValueError):
            max_uses = 0
        if max_uses < 0:
            return refuse(_("A code cannot be used a negative number of times."))
        starts_on = (post.get('starts_on') or '').strip()
        use_until = (post.get('use_until') or '').strip()
        if starts_on and use_until and use_until < starts_on:
            return refuse(_("The last day cannot come before the first."))

        try:
            with request.env.cr.savepoint():
                request.env['modryn.discount.code'].sudo().create({
                    'code': code,
                    'value_kind': kind,
                    'percent': value if kind == 'percent' else 0.0,
                    'amount': value if kind == 'amount' else 0.0,
                    'note': (post.get('note') or '').strip(),
                    'starts_on': starts_on or False,
                    'use_until': use_until or False,
                    'max_uses': max_uses,
                })
        except ValidationError as err:
            return refuse(err.args[0] if err.args else _("That code already exists."))
        return request.redirect('/manage/team-screen?view=codes')

    @http.route('/manage/codes/archive/<int:code_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def code_archive(self, code_id, **post):
        """Retire a code, or bring it back.

        Archived and never deleted: a sale made with it carries the word in its
        reason, and a code removed from the table would leave that sentence
        unexplained on the owner's screen months later.
        """
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        rule = request.env['modryn.discount.code'].sudo().with_context(
            active_test=False).browse(code_id).exists()
        if rule:
            rule.active = not rule.active
        return request.redirect('/manage/team-screen?view=codes')

    @http.route('/manage/shop-details', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def shop_details_save(self, **post):
        """The owner's, like the opening hours and the fitting rooms.

        A manager runs the floor; what the shop tells the world about itself is
        not a floor decision.
        """
        if not access.is_owner():
            return access.deny()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect(
                '/manage/team-screen?view=shop&error=%s'
                % _("The boutique needs a name."))
        request.env.company.sudo().write({
            'name': name,
            # False and not '': an empty string is a value, and every template
            # on the site guards with t-if - which an empty string passes.
            'phone': (post.get('phone') or '').strip() or False,
            'modryn_whatsapp': (post.get('whatsapp') or '').strip() or False,
            'email': (post.get('email') or '').strip() or False,
            'street': (post.get('street') or '').strip() or False,
            'city': (post.get('city') or '').strip() or False,
        })
        return request.redirect('/manage/team-screen?view=shop')

    @http.route('/manage/team-screen', type='http', auth='user', website=True,
                sitemap=False)
    def manager_screen(self, **kw):
        # A MANAGER always, plus whoever the owner grants it to. The screen is
        # named after her: moving it into the bottom row put it behind a grant
        # like everything else down there, and a manager arriving at her own
        # screen to find nothing on it is not a permission decision anybody
        # made. A named exception rather than loosening the whole row - the rest
        # of it is the boutique's administration and stays granted.
        if not (access.is_manager() or access.can_view('boss')):
            return access.deny()
        # The form is a sub-state of the team tile, not a view of its own: a
        # stale ?view=team&edit=<gone> falls back to the cards rather than to an
        # error, because _account_form returns nothing for a person who is not
        # there any more.
        try:
            editing = int(kw['edit']) if kw.get('edit') else None
        except (TypeError, ValueError):
            editing = None
        # Whose hours. Read here rather than in the context builder so an id
        # for somebody who has since left falls back to the picker instead of a
        # traceback - the same fallback the team form's stale ?edit= gets.
        whose = None
        if kw.get('whose'):
            try:
                whose = request.env['hr.employee'].sudo().browse(
                    int(kw['whose'])).exists()
            except (TypeError, ValueError):
                whose = None
        return self._render(view=kw.get('view'), editing=editing,
                            adding=bool(kw.get('new')),
                            archived=bool(kw.get('archived')),
                            whose=whose or None)

    @http.route('/manage/team-screen/announce', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def announce(self, **post):
        if not access.is_manager():
            return access.deny()
        body = (post.get('body') or '').strip()
        # getlist, and replace-set semantics: the same idiom manage.py uses for
        # its page matrix. An empty selection is the meaningful "everyone",
        # not a missing value.
        raw = request.httprequest.form.getlist('employee_ids')
        ids = []
        for value in raw:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        employees = request.env['hr.employee'].sudo().browse(ids).exists()
        if not body:
            return self._render(
                error='empty', draft=body, picked=employees.ids, view='announce')
        record = request.env['modryn.announcement'].sudo().modryn_publish(
            body, employees, self._my_employee())
        if not record:
            # The only way to get here with a body: every person ticked is the
            # author herself. Said out loud rather than redirecting to a list
            # that would show nothing new and leave her wondering.
            return self._render(
                error='nobody', draft=body, picked=employees.ids, view='announce')
        return request.redirect('/manage/team-screen?view=announce')

    # ------------------------------------------------------ her papers
    def _team_member(self, raw_id):
        """One of the boutique's own people, by id from a form. Or nothing.

        Two guards in one place because both are about the same lie: a form
        field is whatever arrives, not what the template put there. int() on a
        non-number is a 500 rather than a refusal, and browsing an id that is
        not staff files a shift against somebody no screen here can show.
        """
        try:
            employee_id = int(raw_id or 0)
        except (TypeError, ValueError):
            return request.env['hr.employee']
        if employee_id <= 0:
            return request.env['hr.employee']
        return request.env['hr.employee'].sudo().with_context(
            active_test=False).search([
                ('id', '=', employee_id),
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
            ], limit=1)

    # ------------------------------------------------- who was on the floor
    def _worked_back(self, employee_id, month=None, error=None):
        """Back to the month she was just looking at."""
        target = '/manage/team-screen?view=worked&whose=%d' % employee_id
        if month:
            target += '&month=%s' % werkzeug.urls.url_quote(month)
        if error:
            target += '&error=%s' % error
        return request.redirect(target)

    @http.route('/manage/team-screen/worked/amend', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def worked_amend(self, **post):
        """Set what a spell really was, or clear it away.

        One route for both because they are one decision on the page - the row
        has a Save and a Remove, and splitting them into two addresses is two
        places to get the permission check right.
        """
        if not access.is_manager():
            return access.deny()
        Attendance = request.env['modryn.shift.attendance']
        try:
            spell = Attendance.sudo().browse(int(post.get('spell_id') or 0)).exists()
        except (TypeError, ValueError):
            spell = None
        if not spell:
            return request.redirect('/manage/team-screen?view=worked')
        employee_id = spell.employee_id.id
        month = post.get('month')

        if post.get('drop'):
            # A mis-tap, deleted rather than zeroed. A spell corrected to no
            # length would still be a line on her month claiming she came in.
            spell.unlink()
            return self._worked_back(employee_id, month)

        day = (post.get('day') or '').strip()
        try:
            day = datetime.strptime(day, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return self._worked_back(employee_id, month, 'badtime')
        error = spell.modryn_amend(day, post.get('came'), post.get('went'))
        return self._worked_back(employee_id, month, error)

    @http.route('/manage/team-screen/worked/add', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def worked_add(self, **post):
        """A shift nobody pressed the button for at either end."""
        if not access.is_manager():
            return access.deny()
        employee = self._team_member(post.get('employee_id'))
        if not employee:
            return request.redirect('/manage/team-screen?view=worked')
        month = post.get('month')
        try:
            day = datetime.strptime(
                (post.get('day') or '').strip(), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return self._worked_back(employee.id, month, 'badtime')
        error = request.env['modryn.shift.attendance'].modryn_add_spell(
            employee, day, post.get('came'), post.get('went'))
        # Land on the month she just wrote INTO, not the one she was reading.
        # Adding a February shift while looking at March and being shown March
        # unchanged reads exactly like the save having failed.
        return self._worked_back(
            employee.id, month if error else day.strftime('%Y-%m'), error)

    @http.route('/manage/team-screen/paper', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def paper_upload(self, **post):
        """File a signed page against one woman's record.

        Still here for the form's own file field to post to. Owner-only, the
        same as the form that draws it.
        """
        if not access.is_owner():
            return access.deny()
        employee = self._team_member(post.get('employee_id'))
        if not employee:
            return request.redirect('/manage/team-screen?view=team')

        uploads = [u for u in request.httprequest.files.getlist('paper')
                   if u and u.filename]
        if not uploads:
            return self._render(view='team', editing=employee.id,
                                form_errors={'paper': _("Choose a file first.")})
        if _file_papers(employee, uploads):
            return self._render(
                view='team', editing=employee.id,
                form_errors={'paper': _("That file is too large — "
                                        "the limit is 10 MB.")})
        return request.redirect(
            '/manage/team-screen?view=team&edit=%d' % employee.id)

    @http.route('/manage/team-screen/paper/<int:attachment_id>', type='http',
                auth='user', website=True, sitemap=False)
    def paper_file(self, attachment_id, **kw):
        """Read one back. Through this route and never /web/content, which
        serves any attachment its ACL allows - and ir.attachment's rules are
        about models, not about which woman owns which payslip."""
        if not access.is_manager():
            return access.deny()
        attachment = request.env['ir.attachment'].sudo().browse(
            attachment_id).exists()
        # It must be a STAFF paper. A manager may read her whole team's folder,
        # but that is not permission to read every attachment in the database
        # by guessing an id.
        if not attachment or attachment.res_model != 'hr.employee':
            return request.not_found()
        return request.make_response(attachment.raw, headers=[
            # Downloaded, never rendered: an uploaded .html or .svg served
            # inline runs in the boutique's own origin, which is stored XSS
            # with extra steps.
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', content_disposition(attachment.name)),
            ('X-Content-Type-Options', 'nosniff'),
        ])

    @http.route('/manage/team-screen/paper/<int:attachment_id>/remove',
                type='http', auth='user', website=True, methods=['POST'],
                csrf=True, sitemap=False)
    def paper_remove(self, attachment_id, **post):
        # The OWNER's. This used to read is_manager, from when the button sat
        # on the card; it is on the account form now, which no manager can open
        # - and a route left one rank wider than the only screen that reaches
        # it is a door with no handle on this side and a handle on that one.
        if not access.is_owner():
            return access.deny()
        attachment = request.env['ir.attachment'].sudo().browse(
            attachment_id).exists()
        if not attachment or attachment.res_model != 'hr.employee':
            return request.redirect('/manage/team-screen?view=team')
        # Read her id BEFORE unlinking: res_id is gone with the row afterwards,
        # and the redirect would land on the cards instead of her form.
        employee_id = attachment.res_id
        attachment.unlink()
        return request.redirect(
            '/manage/team-screen?view=team&edit=%d' % employee_id)

    @http.route('/manage/team-screen/unsend', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def unsend(self, **post):
        """Sent by mistake. Takes it off the bells as well as off this list."""
        if not access.is_manager():
            return access.deny()
        record = request.env['modryn.announcement'].sudo().browse(
            int(post.get('announcement_id') or 0)).exists()
        if record:
            record.modryn_unsend()
        return request.redirect('/manage/team-screen?view=announce')


# The staff section, so a MANAGER gets it without the owner having to grant
# anything - modryn_can_view lets a manager into every staff-section page - and
# the owner can still hand it to a senior saleswoman by role. Not the manage
# section, which is owner-only by design and would shut every manager out of the
# screen named after her.
# The BOTTOM row, with the team, the dresses and the reports - the boutique's own
# administration rather than the shift in front of her. Grantable through the
# owner's matrix like everything else down there, so a senior saleswoman can be
# given it without being made a manager.
nav.register('boss', '/manage/team-screen', _lt("Manager screen"), 12,
             'manage', 'fa-bullhorn')
