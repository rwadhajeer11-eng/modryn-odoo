from datetime import datetime

from odoo import api, fields, models

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
        booking_domain = [
            '&', '&', ('modryn_is_booking', '=', True),
            '&', ('start', '<=', now), ('stop', '>=', now),
            '|', ('modryn_employee_id', 'in', self.ids),
            ('modryn_helper_ids', 'in', self.ids),
        ]
        # Cancelled bookings occupy nobody. The field ships with modryn_portal,
        # which may not be installed in this database.
        if 'modryn_cancelled_at' in Event._fields:
            booking_domain = ['&', ('modryn_cancelled_at', '=', False)] + booking_domain
        bookings = Event.search(booking_domain)

        queue = self.env['modryn.queue.entry'].sudo().search([
            '&', ('state', '=', 'called'),
            '|', ('modryn_employee_id', 'in', self.ids),
            ('modryn_helper_ids', 'in', self.ids),
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

        user = Users.create({
            'name': self.name,
            'login': username,
            'password': password,
            # A user may be portal OR internal, never both — Odoo enforces this
            # with a constraint, so set exactly one base group plus our own.
            'group_ids': [(6, 0, [base_group.id, self._modryn_group_for_level().id])],
        })
        self.user_id = user
        return user
