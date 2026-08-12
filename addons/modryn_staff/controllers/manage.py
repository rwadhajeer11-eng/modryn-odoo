from psycopg2 import IntegrityError

from odoo import _, http
from odoo.addons.modryn_booking.models.opening_hours import weekday_selection
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools import mute_logger

MIN_PASSWORD = 8

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

    def _employee_rows(self):
        employees = request.env['hr.employee'].sudo().with_context(active_test=False).search([])
        return [{
            'id': e.id,
            'name': e.name,
            'phone': e.work_phone or '',
            'role': e.modryn_role_id.name or '',
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
        role_id = post.get('role_id')
        level = post.get('level') or 'staff'

        errors = {}
        if not name:
            errors['name'] = _("Please enter a name.")
        if not username:
            errors['username'] = _("Please enter a username.")
        if len(password) < MIN_PASSWORD:
            errors['password'] = _("Password must be at least %d characters.") % MIN_PASSWORD
        if not role_id:
            errors['role_id'] = _("Please choose a role.")
        if level not in dict(_levels()):
            errors['level'] = _("That permission level isn't valid.")

        if not errors:
            employee = request.env['hr.employee'].sudo().create({
                'name': name,
                'work_phone': (post.get('phone') or '').strip(),
                'modryn_role_id': int(role_id),
                'modryn_level': level,
            })
            try:
                employee.modryn_provision_login(username, password)
            except ValueError as exc:
                # A taken username is a normal outcome of a form, not a 500.
                # The employee row would otherwise survive without a login, so
                # remove it and let the owner correct the field.
                employee.unlink()
                errors['username'] = str(exc)

        if errors:
            return request.render('modryn_staff.manage_staff_form', {
                'roles': self._roles(), 'levels': _levels(), 'employee': None,
                'errors': errors, 'values': post, 'active_tab': 'staff',
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
            'errors': {}, 'values': {
                'name': employee.name,
                'phone': employee.work_phone or '',
                'role_id': employee.modryn_role_id.id,
                'level': employee.modryn_level,
            },
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
        if level not in dict(_levels()):
            errors['level'] = _("That permission level isn't valid.")

        if errors:
            return request.render('modryn_staff.manage_staff_form', {
                'roles': self._roles(), 'levels': _levels(), 'employee': employee,
                'errors': errors, 'values': post, 'active_tab': 'staff',
            })

        level_changed = level != employee.modryn_level
        employee.write({
            'name': name,
            'work_phone': (post.get('phone') or '').strip(),
            'modryn_role_id': int(post['role_id']) if post.get('role_id') else False,
            'modryn_level': level,
        })

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
        return request.render('modryn_staff.manage_roles', {
            'roles': roles, 'error': error, 'active_tab': 'roles',
        })

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
            'active': h.active,
        } for h in windows]

    @http.route('/manage/hours', type='http', auth='user', website=True, sitemap=False)
    def hours_list(self, error=None, **kw):
        if not self._require_owner():
            return request.not_found()
        return request.render('modryn_staff.manage_hours', {
            'hours': self._hour_rows(),
            'weekdays': self._weekdays(),
            'error': error,
            'active_tab': 'hours',
        })

    @http.route('/manage/hours/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def hours_new(self, **post):
        if not self._require_owner():
            return request.not_found()

        weekday = post.get('weekday')
        if weekday not in WEEK_ORDER:
            return request.redirect('/manage/hours?error=%s' % _("Please choose a day"))
        opens = _clock_to_float(post.get('opens'))
        closes = _clock_to_float(post.get('closes'))
        if opens is None or closes is None:
            return request.redirect(
                '/manage/hours?error=%s' % _("Please enter an opening and a closing time"))
        if closes <= opens:
            return request.redirect(
                '/manage/hours?error=%s' % _("A day has to close after it opens"))

        taken = _("You already open at that time on that day")
        Hours = self._hours()
        if Hours.search_count([('weekday', '=', weekday), ('start_hour', '=', opens)]):
            return request.redirect('/manage/hours?error=%s' % taken)
        try:
            # Savepoint so a duplicate that slips past the check above — or any
            # constraint the model enforces — is refused in words rather than
            # poisoning the request's transaction.
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                Hours.create({
                    'weekday': weekday, 'start_hour': opens, 'end_hour': closes})
        except (ValidationError, IntegrityError):
            return request.redirect('/manage/hours?error=%s' % taken)
        return request.redirect('/manage/hours')

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
        return request.redirect('/manage/hours')
