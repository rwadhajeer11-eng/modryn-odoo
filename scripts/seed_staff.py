# Seeds one boutique's people so the walkthrough has real humans in it.
# Runs inside `odoo-bin shell`. Idempotent: re-running skips anyone who exists.
#
#   MODRYN_DEMO_PASSWORD='pick-your-own' MODRYN_SLUG=bella \
#       ./odoo/odoo-bin shell -c odoo.conf -d bella \
#       --db-filter='^bella$' --no-http < scripts/seed_staff.py

import os

SLUG = os.environ.get('MODRYN_SLUG', 'bella')

# No fallback, deliberately. The literal that used to sit here survived every cleanup
# precisely because the script still ran when nobody set anything — a default is how a
# credential gets re-committed, so an unset var has to be fatal rather than convenient.
DEMO_PASSWORD = os.environ.get('MODRYN_DEMO_PASSWORD')
if not DEMO_PASSWORD:
    raise SystemExit(
        "MODRYN_DEMO_PASSWORD is unset. Pick a password for the seeded demo logins, then:\n"
        "    MODRYN_DEMO_PASSWORD='pick-your-own' MODRYN_SLUG=%s"
        " ./odoo/odoo-bin shell -c odoo.conf -d %s"
        " --db-filter='^%s$' --no-http < scripts/seed_staff.py" % (SLUG, SLUG, SLUG))

Employee = env['hr.employee'].sudo()
Role = env['modryn.staff.role'].sudo()
Users = env['res.users'].sudo()


def role(name):
    return Role.search([('name', '=', name)], limit=1) or Role.create({'name': name})


sales = role('מוכרת')
seamstress = role('תופרת')
reception = role('קבלת קהל')

# (name, role, level, username, work_phone)
PEOPLE = {
    'bella': [
        ('מירי לוי',    sales,      'owner',   'miri',  '03-5551234'),
        ('שרה כהן',     sales,      'manager', 'sara',  '052-5550001'),
        ('רותם אברהם',  sales,      'staff',   'rotem', '052-5550002'),
        ('אורלי דוד',   seamstress, 'staff',   'orly',  '052-5550003'),
        ('נועה מזרחי',  reception,  'staff',   'noa',   '052-5550004'),
    ],
    'noga': [
        ('תמר נגה',     sales,      'owner',   'tamar', '03-5559999'),
        ('יעל בר',      sales,      'manager', 'yael',  '053-5550001'),
        ('דנה שמש',     seamstress, 'staff',   'dana',  '053-5550002'),
    ],
}

created = []
for name, job, level, username, phone in PEOPLE[SLUG]:
    if Employee.with_context(active_test=False).search_count([('name', '=', name)]):
        continue

    # The owner reuses the database's existing admin account rather than adding a
    # second internal (billable) seat. Everyone else gets a fresh portal login.
    if level == 'owner':
        admin = env.ref('base.user_admin', raise_if_not_found=False)
        if admin:
            admin.write({
                'login': username,
                'password': DEMO_PASSWORD,
                'group_ids': [(4, env.ref('modryn_staff.group_boutique_owner').id)],
            })
            # Installing `hr` already created an employee for admin, and Odoo
            # enforces one employee per (user, company) — creating a second and
            # pointing it at admin raises hr_employee_user_uniq. Adopt the
            # existing row instead of fighting the constraint.
            employee = Employee.with_context(active_test=False).search(
                [('user_id', '=', admin.id)], limit=1)
            if employee:
                employee.write({
                    'name': name,
                    'work_phone': phone,
                    'modryn_role_id': job.id,
                    'modryn_level': level,
                })
            else:
                employee = Employee.create({
                    'name': name,
                    'work_phone': phone,
                    'modryn_role_id': job.id,
                    'modryn_level': level,
                    'user_id': admin.id,
                })
            created.append((name, level, username, 'internal/admin'))
            continue

    employee = Employee.create({
        'name': name,
        'work_phone': phone,
        'modryn_role_id': job.id,
        'modryn_level': level,
    })
    employee.modryn_provision_login(username, DEMO_PASSWORD)
    created.append((name, level, username, 'portal'))

env.cr.commit()

print('SEEDED_STAFF %s' % SLUG)
for row in created:
    print('  %s | %s | login=%s | %s' % row)
print('TOTAL_EMPLOYEES=%s' % Employee.search_count([]))
print('PORTAL_USERS=%s' % Users.search_count([('group_ids', 'in', env.ref('base.group_portal').ids)]))
