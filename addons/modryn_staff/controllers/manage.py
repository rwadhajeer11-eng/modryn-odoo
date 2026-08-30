from datetime import timedelta

import werkzeug.urls

from psycopg2 import IntegrityError

from odoo import _, fields, http
from odoo.addons.modryn_booking.controllers.main import _utc_on
from odoo.addons.modryn_booking.models.opening_hours import weekday_selection
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools import mute_logger

from .. import nav
from ..models.role_page import ALWAYS_OPEN

MIN_PASSWORD = 8
# The most fittings an owner can claim to run in one hour. Not a business rule
# so much as a typo guard — see _to_capacity.
MAX_CAPACITY = 20

# modryn.opening.hours stores Python weekday() numbers as strings, which start on
# Monday. The Israeli retail week starts on Sunday, so the page reads in this
# order rather than the model's.
WEEK_ORDER = ('6', '0', '1', '2', '3', '4', '5')


def _clock_to_float(raw):
    """"09:30" -> 9.5. None when the field arrives empty or unparseable."""
    try:
        hour, minute = (raw.split(':') + ['0'])[:2]
        value = int(hour) + int(minute) / 60.0
    except (AttributeError, TypeError, ValueError):
        return None
    return value if 0 <= value <= 24 else None


def _fmt_hour(value):
    """9.5 -> "09:30". The inverse of _clock_to_float, for reading back."""
    whole = int(value)
    return '%02d:%02d' % (whole, int(round((value - whole) * 60)))


def _to_date(raw):
    """"2026-09-21" -> date. None when the field arrives empty or unparseable.

    <input type="date"> posts exactly this format, but a hand-crafted POST is
    not obliged to, and a bare strptime would answer that with a traceback.
    """
    try:
        return fields.Date.to_date(raw)
    except (TypeError, ValueError):
        return None


def _to_capacity(raw):
    """"2" -> 2. None when the field is not a whole number of fittings.

    Empty means one, so an owner who never looks at the field keeps the
    behaviour the boutique had before capacity existed. Zero is refused rather
    than accepted as "open but unbookable" — closing the window or adding a
    closure is how a day is taken off the page, and two ways to say it is how
    they drift apart.
    """
    if raw is None or not str(raw).strip():
        return 1
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    # Capped as well as floored. This box sits between two time boxes, so a
    # mistyped "1000" is a live possibility — and both submit paths size a retry
    # loop and a seat scan directly from this number, so an absurd value turns a
    # typo into per-request work no bride benefits from. No boutique fits twenty
    # brides in an hour.
    return value if 1 <= value <= MAX_CAPACITY else None


def _levels():
    """The permission levels, paired with their label.

    Built per request rather than kept as a module constant: _() resolves
    against the language of the caller, so a constant would freeze the labels
    in whatever language happened to be active at import time.
    """
    return [
        ('owner', _("Owner")),
        ('manager', _("Shift manager")),
        ('staff', _("Staff")),
    ]


# The fields that describe the PERSON rather than the job. Collected in one
# place so the hire form and the edit form cannot drift apart - the bug where a
# field is saveable on one screen and silently dropped on the other is exactly
# what a second copy of this dict buys you.
PERSONAL_FIELDS = ('id_number', 'city', 'street', 'backup_phone', 'gender')


def _personal_values(post):
    return {'modryn_%s' % f: (post.get(f) or '').strip() for f in PERSONAL_FIELDS}


def _personal_from(employee):
    return {f: getattr(employee, 'modryn_%s' % f) or '' for f in PERSONAL_FIELDS}


class ModrynManage(http.Controller):
    """Owner-only administration, themed to match the storefront.

    Every route re-checks the owner group server-side. Hiding a button is not a
    permission: a manager who types /manage/staff/new must be refused here, not
    merely fail to find a link.

    Portal users have no ORM access to hr.employee (Odoo restricts it to
    hr.group_hr_user, and it carries private HR fields), so all reads and writes
    go through sudo() and the templates only ever receive plain dicts.
    """

    # ---------------------------------------------------------------- helpers
    def _require_owner(self):
        user = request.env.user
        if user._is_public() or not user.has_group('modryn_staff.group_boutique_owner'):
            return False
        return True

    def _roles(self):
        return request.env['modryn.staff.role'].sudo().search([])

    def _role_ids_from(self, post):
        """Every role ticked on the form. Replace-set, never a toggle.

        getlist, because a repeated field name is how an HTML form says
        "several of these" - the same idiom the page-grant matrix already uses.
        Validated against the roles that actually exist, so a hand-made POST
        cannot attach a role id from another tenant's numbering.
        """
        wanted = {int(r) for r in request.httprequest.form.getlist('role_ids')
                  if str(r).isdigit()}
        return list(wanted & set(self._roles().ids))

    def _id_number_taken(self, number, employee=None):
        """The friendly half of the ID-number rule.

        hr.employee carries the real constraint - this is only here so the owner
        reads a sentence in the form instead of meeting a traceback. The model
        keeps the last word, including for the race this cannot see.

        active_test=False on purpose: a number belonging to somebody who left
        last year is still taken, and without it the form would happily create
        the duplicate that the model then refuses.
        """
        number = (number or '').strip()
        if not number:
            return None
        domain = [('modryn_id_number', '=ilike', number)]
        if employee:
            domain.append(('id', '!=', employee.id))
        if request.env['hr.employee'].sudo().with_context(
                active_test=False).search_count(domain):
            return _("Somebody on the team already has that ID number.")
        return None

    def _employee_rows(self):
        employees = request.env['hr.employee'].sudo().with_context(active_test=False).search([])
        return [{
            'id': e.id,
            'name': e.name,
            'phone': e.work_phone or '',
            'backup_phone': e.modryn_backup_phone or '',
            'city': e.modryn_city or '',
            'street': e.modryn_street or '',
            # Deliberately NOT in the list: modryn_id_number. The list is the
            # screen left open on the counter all day, and an identity number
            # is the one field here that is worth something to somebody who
            # walks past it. It lives on the form, one click away.
            'role': ' · '.join(e.modryn_role_ids.mapped('name')) or '',
            'level': dict(_levels()).get(e.modryn_level, e.modryn_level or ''),
            'level_raw': e.modryn_level or '',
            'login': e.user_id.login or '',
            'active': e.active,
            'occupied': e.modryn_is_occupied,
            'occupied_with': e.modryn_occupied_with or '',
        } for e in employees]

    # ------------------------------------------------------------------ staff
    @http.route('/manage/staff', type='http', auth='user', website=True, sitemap=False)
    def staff_list(self, **kw):
        if not self._require_owner():
            return request.not_found()
        return request.render('modryn_staff.manage_staff_list', {
            'employees': self._employee_rows(),
            'active_tab': 'staff',
        })

    @http.route('/manage/staff/new', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def staff_new_form(self, **kw):
        if not self._require_owner():
            return request.not_found()
        return request.render('modryn_staff.manage_staff_form', {
            'roles': self._roles(), 'levels': _levels(), 'employee': None,
            'errors': {}, 'values': {}, 'active_tab': 'staff',
        })

    @http.route('/manage/staff/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def staff_new_submit(self, **post):
        if not self._require_owner():
            return request.not_found()

        name = (post.get('name') or '').strip()
        username = (post.get('username') or '').strip()
        password = post.get('password') or ''
        level = post.get('level') or 'staff'

        errors = {}
        if not name:
            errors['name'] = _("Please enter a name.")
        if not username:
            errors['username'] = _("Please enter a username.")
        if len(password) < MIN_PASSWORD:
            errors['password'] = _("Password must be at least %d characters.") % MIN_PASSWORD
        role_ids = self._role_ids_from(post)
        if not role_ids:
            errors['role_id'] = _("Please choose at least one role.")
        if level not in dict(_levels()):
            errors['level'] = _("That permission level isn't valid.")
        id_error = self._id_number_taken(post.get('id_number'))
        if id_error:
            errors['id_number'] = id_error

        if not errors:
            # The personal fields go straight into create(), unlike work_phone
            # below: they are stored columns on hr_employee, so provision_login
            # relinking the work CONTACT cannot take them with it.
            employee = request.env['hr.employee'].sudo().create(dict(
                _personal_values(post),
                name=name,
                modryn_role_ids=[(6, 0, role_ids)],
                modryn_level=level,
            ))
            try:
                employee.modryn_provision_login(username, password)
            except ValueError as exc:
                # A taken username is a normal outcome of a form, not a 500.
                # The employee row would otherwise survive without a login, so
                # remove it and let the owner correct the field.
                employee.unlink()
                errors['username'] = str(exc)
            else:
                # AFTER provisioning, never in create(): work_phone lives on
                # the employee's work contact, and provision_login relinks
                # that contact to the new portal user's partner — a phone
                # written at create is silently dropped. Every walkthrough
                # hire made through this form lost her number exactly here,
                # and a staff member with no phone is one the assignment SMS
                # can only log-and-skip for.
                phone = (post.get('phone') or '').strip()
                if phone:
                    employee.work_phone = phone

        if errors:
            return request.render('modryn_staff.manage_staff_form', {
                'roles': self._roles(), 'levels': _levels(), 'employee': None,
                # role_ids OVER the raw post, deliberately. A form field arrives
                # as a string, the template asks `role.id in values['role_ids']`,
                # and an int against a string is a 500 - so a hire that trips any
                # validation would crash instead of showing the error. It also
                # keeps her ticks: re-rendering from the raw post dropped every
                # role the owner had chosen.
                'errors': errors, 'values': dict(post, role_ids=role_ids),
                'active_tab': 'staff',
            })
        return request.redirect('/manage/staff')

    @http.route('/manage/staff/edit/<int:employee_id>', type='http', auth='user',
                website=True, methods=['GET'], sitemap=False)
    def staff_edit_form(self, employee_id, **kw):
        if not self._require_owner():
            return request.not_found()
        employee = request.env['hr.employee'].sudo().with_context(
            active_test=False).browse(employee_id).exists()
        if not employee:
            return request.not_found()
        return request.render('modryn_staff.manage_staff_form', {
            'roles': self._roles(), 'levels': _levels(), 'employee': employee,
            'errors': {}, 'values': dict(
                _personal_from(employee),
                name=employee.name,
                phone=employee.work_phone or '',
                role_ids=employee.modryn_role_ids.ids,
                level=employee.modryn_level,
            ),
            'active_tab': 'staff',
        })

    @http.route('/manage/staff/edit/<int:employee_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def staff_edit_submit(self, employee_id, **post):
        if not self._require_owner():
            return request.not_found()
        employee = request.env['hr.employee'].sudo().with_context(
            active_test=False).browse(employee_id).exists()
        if not employee:
            return request.not_found()

        name = (post.get('name') or '').strip()
        level = post.get('level') or employee.modryn_level
        errors = {}
        if not name:
            errors['name'] = _("Please enter a name.")
        # The same rule the hire form has. Without it, an owner who cleared the
        # ticks by accident saved an employee with NO role - and a role-less
        # woman silently loses every page but her own home and profile, because
        # modryn_can_view falls straight to `if not roles: return False`. Empty
        # is not a state the boutique ever means, so it is refused here rather
        # than discovered by the worker who cannot open her own roster.
        role_ids = self._role_ids_from(post)
        if not role_ids:
            errors['role_id'] = _("Please choose at least one role.")
        if level not in dict(_levels()):
            errors['level'] = _("That permission level isn't valid.")
        id_error = self._id_number_taken(post.get('id_number'), employee=employee)
        if id_error:
            errors['id_number'] = id_error

        if errors:
            return request.render('modryn_staff.manage_staff_form', {
                'roles': self._roles(), 'levels': _levels(), 'employee': employee,
                'errors': errors, 'values': dict(post, role_ids=role_ids),
                'active_tab': 'staff',
            })

        level_changed = level != employee.modryn_level
        employee.write(dict(
            _personal_values(post),
            name=name,
            work_phone=(post.get('phone') or '').strip(),
            modryn_role_ids=[(6, 0, role_ids)],
            modryn_level=level,
        ))

        # A promotion has to move the underlying account, or the new level is a
        # label with no power behind it.
        if level_changed and employee.user_id:
            new_group = employee._modryn_group_for_level()
            all_levels = request.env['res.groups']
            for xmlid in ('modryn_staff.group_boutique_owner',
                          'modryn_staff.group_shift_manager',
                          'modryn_staff.group_boutique_staff'):
                all_levels |= request.env.ref(xmlid)
            employee.user_id.sudo().write({
                'group_ids': [(3, g.id) for g in all_levels] + [(4, new_group.id)],
            })

        return request.redirect('/manage/staff')

    @http.route('/manage/staff/archive/<int:employee_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def staff_archive(self, employee_id, **post):
        if not self._require_owner():
            return request.not_found()
        employee = request.env['hr.employee'].sudo().with_context(
            active_test=False).browse(employee_id).exists()
        if employee:
            # Archive, never delete: past assignments still point here, and a
            # deleted employee would make yesterday's bookings unreadable.
            employee.active = not employee.active
            if employee.user_id:
                employee.user_id.sudo().active = employee.active
        return request.redirect('/manage/staff')

    # ------------------------------------------------------------------ roles
    @http.route('/manage/roles', type='http', auth='user', website=True, sitemap=False)
    def roles_list(self, error=None, **kw):
        if not self._require_owner():
            return request.not_found()
        roles = request.env['modryn.staff.role'].sudo().with_context(
            active_test=False).search([])
        # The access matrix: BOTH rows of the navbar now. It used to be the top
        # row only, so an owner who wanted her manager in Dresses or Reports had
        # nowhere to say so. 'home' and 'profile' are never columns - they
        # cannot be configured away - and nav.NEVER_GRANTABLE holds the two that
        # would let a tick grant the power to tick.
        pages = [p for p in nav.grantable() if p['key'] not in ALWAYS_OPEN]
        granted = {}
        for grant in request.env['modryn.role.page'].sudo().search(
                [('role_id', 'in', roles.ids)]):
            granted.setdefault(grant.role_id.id, []).append(grant.page_key)
        return request.render('modryn_staff.manage_roles', {
            'roles': roles, 'error': error, 'active_tab': 'roles',
            'pages': pages, 'granted': granted,
        })

    @http.route('/manage/roles/pages', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def roles_pages(self, **post):
        """Replace-set semantics: what is ticked when Save lands is what
        stands. Simpler to reason about than per-checkbox toggles, and the
        whole grid travels in one POST."""
        if not self._require_owner():
            return request.not_found()
        valid_keys = {p['key'] for p in nav.grantable()
                      if p['key'] not in ALWAYS_OPEN}
        Role = request.env['modryn.staff.role'].sudo().with_context(
            active_test=False)
        role_ids = set(Role.search([]).ids)
        wanted = set()
        for token in request.httprequest.form.getlist('pages'):
            role_str, _sep, key = token.partition(':')
            if key in valid_keys and role_str.isdigit() \
                    and int(role_str) in role_ids:
                wanted.add((int(role_str), key))
        Grant = request.env['modryn.role.page'].sudo()
        have = {(g.role_id.id, g.page_key): g
                for g in Grant.search([('role_id', 'in', list(role_ids))])}
        for pair, grant in have.items():
            if pair not in wanted:
                grant.unlink()
        for pair in wanted:
            if pair not in have:
                Grant.create({'role_id': pair[0], 'page_key': pair[1]})
        return request.redirect('/manage/roles')

    @http.route('/manage/roles/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def roles_new(self, **post):
        if not self._require_owner():
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/roles?error=%s' % _("Please enter a role name."))
        try:
            # Savepoint so a rejected duplicate does not poison the request's
            # transaction. Uniqueness is a Python @api.constrains (see the model
            # for why it cannot be a SQL constraint here), so the failure arrives
            # as ValidationError rather than IntegrityError.
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                request.env['modryn.staff.role'].sudo().create({'name': name})
        except (ValidationError, IntegrityError):
            return request.redirect('/manage/roles?error=%s' % _("That role already exists."))
        return request.redirect('/manage/roles')

    @http.route('/manage/roles/archive/<int:role_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def roles_archive(self, role_id, **post):
        if not self._require_owner():
            return request.not_found()
        role = request.env['modryn.staff.role'].sudo().with_context(
            active_test=False).browse(role_id).exists()
        if role:
            role.active = not role.active
        return request.redirect('/manage/roles')

    @http.route('/manage/roles/workshop/<int:role_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def roles_workshop(self, role_id, **post):
        """Toggle whether this role's members take the workshop's task queue."""
        if not self._require_owner():
            return request.not_found()
        role = request.env['modryn.staff.role'].sudo().with_context(
            active_test=False).browse(role_id).exists()
        if role:
            role.is_workshop = not role.is_workshop
        return request.redirect('/manage/roles')

    # -------------------------------------------------------- fitting rooms
    @http.route('/manage/rooms', type='http', auth='user', website=True, sitemap=False)
    def rooms_list(self, error=None, **kw):
        if not self._require_owner():
            return request.not_found()
        return request.render('modryn_staff.manage_rooms', {
            'rooms': request.env['modryn.fitting.room'].sudo().with_context(
                active_test=False).search([]),
            'error': error,
            'active_tab': 'rooms',
        })

    @http.route('/manage/rooms/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def rooms_new(self, **post):
        if not self._require_owner():
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/manage/rooms?error=%s' % _("Please enter a name"))
        Room = request.env['modryn.fitting.room'].sudo()
        if Room.with_context(active_test=False).search_count([('name', '=ilike', name)]):
            return request.redirect(
                '/manage/rooms?error=%s' % _("That fitting room already exists"))
        Room.create({'name': name})
        return request.redirect('/manage/rooms')

    @http.route('/manage/rooms/archive/<int:room_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def rooms_archive(self, room_id, **post):
        if not self._require_owner():
            return request.not_found()
        room = request.env['modryn.fitting.room'].sudo().with_context(
            active_test=False).browse(room_id).exists()
        if room:
            # Archive, never delete: cards still point at this room.
            room.active = not room.active
        return request.redirect('/manage/rooms')

    # -------------------------------------------------------- opening hours
    def _hours(self):
        # active_test=False throughout: a closed window still has to be listed,
        # or the owner cannot reopen it, and a duplicate check that ignores
        # archived rows would still hit the model's uniqueness constraint.
        return request.env['modryn.opening.hours'].sudo().with_context(active_test=False)

    def _weekdays(self):
        """(value, label) pairs, Sunday first. Labels come from the model so the
        page and the model never drift apart on what '6' means.

        Called directly rather than through fields_get(): Odoo resolves a
        callable selection by handing it the recordset, so the round trip only
        added a query and a way to crash. WEEK_ORDER, not the model's _order,
        because that sorts the weekday STRING and would put Monday first.
        """
        labels = dict(weekday_selection())
        return [(day, labels.get(day, day)) for day in WEEK_ORDER]

    def _hour_rows(self):
        Hours = self._hours()
        labels = dict(self._weekdays())
        order = {day: index for index, day in enumerate(WEEK_ORDER)}
        windows = Hours.search([]).sorted(
            key=lambda h: (order.get(h.weekday, len(WEEK_ORDER)), h.start_hour))
        return [{
            'id': h.id,
            'day': labels.get(h.weekday, h.weekday),
            'opens': Hours.modryn_hour_label(h.start_hour),
            'closes': Hours.modryn_hour_label(h.end_hour),
            'capacity': h.capacity,
            'active': h.active,
        } for h in windows]

    def _closure_rows(self):
        # active_test=False for the hours reason: an archived closure still has
        # to be listed, or last year's Yom Kippur cannot be switched back on.
        closures = request.env['modryn.closure'].sudo().with_context(
            active_test=False).search([])
        return [{
            'id': c.id,
            'name': c.name,
            # dd.mm.yyyy, the way the rest of the boutique prints a date. One
            # date when it is one day: making her read "21.09 - 21.09" to learn
            # the shop shuts for an afternoon is noise.
            'when': c.date_from.strftime('%d.%m.%Y') if c.date_from == c.date_to else '%s – %s' % (
                c.date_from.strftime('%d.%m.%Y'), c.date_to.strftime('%d.%m.%Y')),
            # Blank for a full day. Shown ONLY for a part-day closure, because
            # printing "00:00–00:00" against every ordinary closure would read as
            # a bug, and printing nothing against a part-day one would hide the
            # single fact that distinguishes it.
            'hours': '' if c.full_day else '%s – %s' % (
                _fmt_hour(c.start_hour), _fmt_hour(c.end_hour)),
            'active': c.active,
        } for c in closures]

    # The key the two forms on this page park a rejected attempt under.
    HOURS_FORM = 'modryn_hours_form'

    def _hours_bounce(self, post, message):
        """Back to the page with the message AND what she had typed.

        Both forms here redirect on a validation failure, which loses every
        field - a closure has five, and being told one of them is wrong is not a
        reason to take the other four away.

        The session, not the query string: a closure's reason is the boutique's
        own words, and a URL is the one thing on a web server that reaches an
        access log, the browser's history and whatever she pastes into a message
        asking for help.
        """
        request.session[self.HOURS_FORM] = dict(post)
        return request.redirect('/manage/team-screen?view=hours&error=%s' % message)

    def hours_context(self, error=None):
        """Everything the opening-hours panel needs, wherever it is drawn.

        Public because the manager's screen renders that panel now and this is
        where the rows are built - one builder, two readers, rather than a
        second copy that starts disagreeing about which days are closed.
        """
        return {
            'hours': self._hour_rows(),
            'weekdays': self._weekdays(),
            'closures': self._closure_rows(),
            'hours_error': error,
            # POPPED, not read: it re-fills the form exactly once. Left in place
            # it would resurrect a rejected attempt days later, on a page she
            # opened for something else entirely.
            'hours_values': request.session.pop(self.HOURS_FORM, {}) if error else {},
        }

    @http.route('/manage/hours', type='http', auth='user', website=True, sitemap=False)
    def hours_list(self, error=None, **kw):
        """The old address. Somebody has it in a tab, so it moves her along
        rather than 404ing at her."""
        if not self._require_owner():
            return request.not_found()
        target = '/manage/team-screen?view=hours'
        if error:
            target += '&error=%s' % werkzeug.urls.url_quote(error)
        return request.redirect(target)

    @http.route('/manage/hours/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def hours_new(self, **post):
        if not self._require_owner():
            return request.not_found()

        weekday = post.get('weekday')
        if weekday not in WEEK_ORDER:
            return self._hours_bounce(post, _("Please choose a day"))
        opens = _clock_to_float(post.get('opens'))
        closes = _clock_to_float(post.get('closes'))
        if opens is None or closes is None:
            return self._hours_bounce(
                post, _("Please enter an opening and a closing time"))
        if closes <= opens:
            return self._hours_bounce(
                post, _("A day has to close after it opens"))
        capacity = _to_capacity(post.get('capacity'))
        # Checked here as well as by the model's @api.constrains, for the reason
        # closure_new() checks its dates: the owner reads a sentence, not a
        # traceback.
        if capacity is None:
            return self._hours_bounce(post, _(
                "Please say how many fittings you can take at once — one or more"))

        taken = _("You already open at that time on that day")
        Hours = self._hours()
        if Hours.search_count([('weekday', '=', weekday), ('start_hour', '=', opens)]):
            return self._hours_bounce(post, taken)
        try:
            # Savepoint so a duplicate that slips past the check above — or any
            # constraint the model enforces — is refused in words rather than
            # poisoning the request's transaction.
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                Hours.create({
                    'weekday': weekday, 'start_hour': opens, 'end_hour': closes,
                    'capacity': capacity})
        except (ValidationError, IntegrityError):
            return self._hours_bounce(post, taken)
        return request.redirect('/manage/team-screen?view=hours')

    @http.route('/manage/hours/capacity/<int:hours_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def hours_capacity(self, hours_id, **post):
        """Change how many fittings an EXISTING window takes.

        Without this the feature is unreachable on every boutique that exists:
        the five seeded windows are all capacity 1, hours_new refuses a duplicate
        (weekday, start_hour) and the model constrains it too — counting archived
        rows — so a window can never be re-created to carry a different number.
        A one-field POST rather than a general edit form: capacity is the only
        thing about a window that a boutique changes week to week.
        """
        if not self._require_owner():
            return request.not_found()
        window = self._hours().browse(hours_id).exists()
        if not window:
            return request.not_found()
        capacity = _to_capacity(post.get('capacity'))
        if capacity is None:
            return self._hours_bounce(post, (
                _("Fittings at once has to be a whole number between 1 and %d.") % MAX_CAPACITY))
        # Lowering it below what is already booked cancels nothing — those
        # fittings stand, and the hour simply stops being offered until they
        # drain. Refusing here would strand an owner who has just lost a
        # fitting room and cannot say so.
        window.capacity = capacity
        return request.redirect('/manage/team-screen?view=hours')

    @http.route('/manage/hours/archive/<int:hours_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def hours_archive(self, hours_id, **post):
        if not self._require_owner():
            return request.not_found()
        window = self._hours().browse(hours_id).exists()
        if window:
            # Archive, never delete: a boutique closes a window for a holiday
            # week and wants it back, and bookings already taken in it must
            # still read correctly.
            window.active = not window.active
        return request.redirect('/manage/team-screen?view=hours')

    # ------------------------------------------------------------- closures
    @http.route('/manage/hours/closure/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def closure_new(self, **post):
        if not self._require_owner():
            return request.not_found()

        name = (post.get('name') or '').strip()
        if not name:
            return self._hours_bounce(
                post, _("Please say what the closure is for"))
        date_from = _to_date(post.get('date_from'))
        if date_from is None:
            return self._hours_bounce(post, _("Please choose a date"))
        # One day is the common case, so an empty second field means "just that
        # day" rather than an error. Typing the same date twice is the kind of
        # friction that leaves a feature unused.
        date_to = _to_date(post.get('date_to')) or date_from
        # Checked HERE and not only by the model's @api.constrains, so the owner
        # reads a sentence instead of meeting a traceback.
        if date_to < date_from:
            return self._hours_bounce(
                post, _("A closure has to end on or after the day it starts"))
        # Both hours empty means the whole day — what a closure has always been,
        # and what every row written before today still is. One hour without the
        # other is a half-typed form, not a closure "from 14:00 to whenever".
        start_hour = _clock_to_float(post.get('start_hour'))
        end_hour = _clock_to_float(post.get('end_hour'))
        full_day = start_hour is None or end_hour is None
        if not full_day and end_hour <= start_hour:
            return self._hours_bounce(
                post, _("A part-day closure has to end after it starts"))
        # Refuse to shut a day that already has brides booked into it, and say
        # how many. A closure only stops a date being OFFERED — it cancels
        # nothing — so without this the fittings survive, the 24h reminder cron
        # texts each of those brides to come tomorrow, and they arrive at a dark
        # shop. Cancelling has to stay the owner's own act on the floor, because
        # that is the path that actually tells the bride and releases her slot to
        # the day's waitlist. So: cancel first, then close. Refusing enforces the
        # order; a warning she can click past would not.
        Event = request.env['calendar.event'].sudo()
        live = [('modryn_is_booking', '=', True), ('modryn_cancelled_at', '=', False)]
        if full_day:
            booked = Event.search_count(live + [
                ('start', '>=', fields.Date.to_string(date_from)),
                # calendar_event.start is a UTC datetime and these are local dates:
                # compare against the day AFTER date_to so the whole closing day is
                # inside the window whatever the offset is doing that week.
                ('start', '<', fields.Date.to_string(date_to + timedelta(days=1))),
            ])
        else:
            # Only fittings inside the hours actually being closed. Counting the
            # whole day would refuse "we shut at 14:00" because of a 10:00
            # fitting that the closure does not touch at all.
            #
            # One query per day rather than one for the range: the boundary is a
            # LOCAL wall-clock hour and the column is UTC, so each date converts
            # separately — Israel observes DST and a range spanning the change
            # would be an hour wrong on one side of it. _utc_on is imported from
            # modryn_booking rather than re-derived here for exactly that reason.
            booked = 0
            day = date_from
            while day <= date_to:
                booked += Event.search_count(live + [
                    ('start', '>=', _utc_on(day, start_hour)),
                    ('start', '<', _utc_on(day, end_hour)),
                ])
                day += timedelta(days=1)
        if booked:
            return self._hours_bounce(post, (
                _("%s fittings are already booked on those dates. Cancel them from the floor"
                  " first — that is what tells each bride and frees her slot.") % booked))
        # Savepoint for roles_new()'s reason: a constraint the model enforces must
        # be refused in words rather than poison the request's transaction.
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                request.env['modryn.closure'].sudo().create({
                    'name': name, 'date_from': date_from, 'date_to': date_to,
                    'full_day': full_day,
                    'start_hour': 0.0 if full_day else start_hour,
                    'end_hour': 0.0 if full_day else end_hour})
        except (ValidationError, IntegrityError):
            return self._hours_bounce(post, _("Those dates aren't valid"))
        return request.redirect('/manage/team-screen?view=hours')

    @http.route('/manage/hours/closure/archive/<int:closure_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def closure_archive(self, closure_id, **post):
        if not self._require_owner():
            return request.not_found()
        closure = request.env['modryn.closure'].sudo().with_context(
            active_test=False).browse(closure_id).exists()
        if closure:
            # Archive, never delete: a holiday recurs, and an owner who reopens
            # a date wants last year's row back rather than a retyped one.
            closure.active = not closure.active
        return request.redirect('/manage/team-screen?view=hours')
