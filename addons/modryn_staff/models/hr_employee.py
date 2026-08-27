from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

LEVEL_OWNER = 'owner'
LEVEL_MANAGER = 'manager'
LEVEL_STAFF = 'staff'

# Which security group each level grants. Owner is the only internal user.
LEVEL_GROUPS = {
    LEVEL_OWNER: 'modryn_staff.group_boutique_owner',
    LEVEL_MANAGER: 'modryn_staff.group_shift_manager',
    LEVEL_STAFF: 'modryn_staff.group_boutique_staff',
}


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    modryn_role_id = fields.Many2one(
        'modryn.staff.role',
        string="Boutique role",
        help="Owner-defined job role, for example Sales or Seamstress.",
    )
    modryn_level = fields.Selection(
        selection=[
            (LEVEL_OWNER, "Owner"),
            (LEVEL_MANAGER, "Shift manager"),
            (LEVEL_STAFF, "Staff"),
        ],
        string="Permission level",
        default=LEVEL_STAFF,
    )

    # --------------------------------------------------- who she actually is
    # Plain Char columns on hr.employee, deliberately NOT Odoo's own
    # identification_id / private_street / private_phone. Two reasons, and the
    # first one has already bitten this file once:
    #
    #   * work_phone is a RELATED field living on the employee's work contact,
    #     and modryn_provision_login relinks that contact to the new portal
    #     user's partner - so a value written before provisioning is silently
    #     dropped. See the comment on manage.py's hire path, which exists
    #     because every walkthrough hire lost her number exactly there. A stored
    #     Char on this table cannot go missing that way.
    #   * This install carries a slim hr: there is no identification_id column
    #     and no private_street/private_city at all. Reaching for them would
    #     mean depending on modules the boutique does not have.
    modryn_id_number = fields.Char(
        string="ID number",
        help="Identity-card or passport number. Only the owner can see this.",
    )
    modryn_city = fields.Char(string="City")
    # Street AND house number in ONE field, as the owner asked for it ("which
    # street with the number"). Splitting them would make the form longer for no
    # gain: nothing in this product sorts or searches by house number, and a
    # boutique writes an address the way it would write it on an envelope.
    modryn_street = fields.Char(string="Street and number")
    # The number to try when the first one does not answer. Its own field rather
    # than Odoo's emergency_phone: an emergency contact is somebody ELSE - a
    # relative to call if she is hurt at work - and a second number for HER is a
    # different fact. Storing one in the other's column would eventually get
    # somebody's mother called about a shift swap.
    modryn_backup_phone = fields.Char(string="Backup phone")
    # Its own field rather than Odoo's hr gender: this install carries a slim
    # hr with no gender column at all, and the selection here is the boutique's
    # own - the team is overwhelmingly women and "prefer not to say" is a real
    # answer that a two-value field cannot hold.
    modryn_gender = fields.Selection(
        selection=[('female', "Female"), ('male', "Male"), ('other', "Prefer not to say")],
        string="Gender")

    @api.constrains('modryn_id_number')
    def _check_id_number_unique(self):
        """Two people cannot share an ID number.

        Python and not a SQL unique(): blanks must stay repeatable - most of the
        team will have no number recorded on the day this ships - and the check
        has to see ARCHIVED colleagues too, which a search() in a constraint
        only does with active_test=False.
        """
        for employee in self:
            number = (employee.modryn_id_number or '').strip()
            if not number:
                continue
            clash = self.with_context(active_test=False).search_count([
                ('id', '!=', employee.id),
                ('modryn_id_number', '=ilike', number),
            ])
            if clash:
                raise ValidationError(
                    _("Somebody on the team already has that ID number."))

    # Computed and NOT stored, with no @api.depends: occupancy is a fact about
    # *other* records (a live queue entry, a booking happening right now) and
    # about the clock. A stored field would need invalidating every minute and
    # would be wrong the rest of the time. Deriving it means it cannot drift out
    # of sync the way a manual זמין/תפוס toggle does.
    modryn_is_occupied = fields.Boolean(
        string="Occupied",
        compute='_compute_modryn_is_occupied',
    )
    modryn_occupied_with = fields.Char(
        string="Serving",
        compute='_compute_modryn_is_occupied',
    )

    def _compute_modryn_is_occupied(self):
        now = datetime.utcnow()
        Event = self.env['calendar.event'].sudo()

        # Busy means on the FLOOR with a customer — primary or helper on a live
        # booking or a called walk-in. Alteration work deliberately does not
        # count: a seamstress sewing in the back is still callable, and her
        # workshop load shows on the atelier dashboard instead.
        # Helpers are found through modryn.floor.helper, NOT by putting
        # modryn_helper_ids in a domain: that field is a non-stored compute over
        # the through-model, and Odoo cannot turn a non-stored field into SQL —
        # it raises "Cannot convert ... to SQL because it is not stored" and
        # takes the whole floor board down with it.
        Link = self.env['modryn.floor.helper'].sudo()
        helper_links = Link.search([('employee_id', 'in', self.ids)])
        helper_event_ids = helper_links.mapped('event_id').ids
        helper_entry_ids = helper_links.mapped('entry_id').ids

        booking_domain = [
            '&', '&', ('modryn_is_booking', '=', True),
            '&', ('start', '<=', now), ('stop', '>=', now),
            '|', ('modryn_employee_id', 'in', self.ids),
            ('id', 'in', helper_event_ids),
        ]
        # Cancelled bookings occupy nobody. The field ships with modryn_portal,
        # which may not be installed in this database.
        if 'modryn_cancelled_at' in Event._fields:
            booking_domain = ['&', ('modryn_cancelled_at', '=', False)] + booking_domain
        bookings = Event.search(booking_domain)

        queue = self.env['modryn.queue.entry'].sudo().search([
            '&', ('state', '=', 'called'),
            '|', ('modryn_employee_id', 'in', self.ids),
            ('id', 'in', helper_entry_ids),
        ])

        busy = {}
        for record in list(bookings) + list(queue):
            people = record.modryn_employee_id | record.modryn_helper_ids
            for person in people:
                busy.setdefault(person.id, record.name)

        for employee in self:
            employee.modryn_is_occupied = employee.id in busy
            employee.modryn_occupied_with = busy.get(employee.id, False)

    # ------------------------------------------------------------------ users
    def _modryn_group_for_level(self):
        self.ensure_one()
        xmlid = LEVEL_GROUPS.get(self.modryn_level or LEVEL_STAFF)
        return self.env.ref(xmlid)

    def modryn_provision_login(self, username, password):
        """Give this employee an account she can actually sign in with.

        Owners become INTERNAL users (they need the back office for
        configuration Odoo already does well). Managers and staff become PORTAL
        users: free under Enterprise, and structurally unable to reach /odoo,
        so a saleswoman cannot wander into the raw back office.
        """
        self.ensure_one()
        Users = self.env['res.users'].sudo()

        if Users.with_context(active_test=False).search_count([('login', '=', username)]):
            raise ValueError("That username is already taken.")

        if self.modryn_level == LEVEL_OWNER:
            base_group = self.env.ref('base.group_user')
        else:
            base_group = self.env.ref('base.group_portal')

        vals = {
            'name': self.name,
            'login': username,
            'password': password,
            # A user may be portal OR internal, never both — Odoo enforces this
            # with a constraint, so set exactly one base group plus our own.
            'group_ids': [(6, 0, [base_group.id, self._modryn_group_for_level().id])],
        }
        # A new account starts in English; she picks her own language from the
        # navbar and it persists on res.users.lang. Guarded on the language
        # actually being switched on: writing a code the tenant never activated
        # leaves her with a preference that renders nothing.
        if self.env['res.lang'].sudo().search_count([('code', '=', 'en_US')]):
            vals['lang'] = 'en_US'
        user = Users.create(vals)
        self.user_id = user
        return user
