{
    'name': 'MODRYN Staff',
    'summary': 'Employees, owner-defined roles, and assigning staff to customers.',
    'description': """
The staff layer the PoC was missing entirely: before this, a boutique had no
employees, no way to say who serves whom, and every booking was silently
"organized by" the anonymous public website user.

Three permission levels, deliberately cheap:
  owner    internal user  — adds staff, invents roles, sees everything  (/manage)
  manager  PORTAL user    — assigns staff to customers, dispatches walk-ins (/floor)
  staff    PORTAL user    — sees her own assignments (/floor)

Portal users are free even under Odoo Enterprise, so a ten-person boutique costs
one seat, not ten. Portal accounts also physically cannot reach Odoo's back
office, which is the point: staff only ever see the themed terminal.

SECURITY NOTE: hr.employee is ACL-restricted to hr.group_hr_user, and several of
its fields are private HR data. Rather than granting portal users direct ORM
access, every portal-facing controller checks group membership itself and then
reads through sudo(), handing the template only the fields it needs. Portal users
therefore never touch hr.employee directly.
""",
    'category': 'Website',
    'version': '19.0.1.2.0',
    'depends': [
        'hr',
        'portal',
        'modryn_theme',
        'modryn_booking',
        'modryn_queue_poc',
    ],
    'data': [
        'security/modryn_groups.xml',
        'security/ir.model.access.csv',
        'data/staff_role_data.xml',
        'data/fitting_room_data.xml',
        'data/ir_cron_data.xml',
        'views/auth_templates.xml',
        'views/manage_templates.xml',
        'views/floor_templates.xml',
    ],
    'assets': {
        # A glob so the OWL component's .xml template is picked up alongside its
        # .js — a public_components component whose template never loads mounts
        # into an empty box with no error.
        'web.assets_frontend': [
            'modryn_staff/static/src/floor/**/*',
        ],
    },
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
