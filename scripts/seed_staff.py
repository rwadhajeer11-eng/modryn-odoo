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
    # Search in he_IL: after the translation fixup below, the en_US value is an
    # English label, and a bare search (shell default lang) would miss the role
    # and create a duplicate on re-run. Before the fixup he_IL falls back to
    # the stored value, so this finds the role in both states.
    found = Role.with_context(lang='he_IL').search([('name', '=', name)], limit=1)
    return found or Role.create({'name': name})


sales = role('מוכרת')
seamstress = role('תופרת')
reception = role('קבלת קהל')

# Always-run fixups, BEFORE the per-person existence skip — they repair tenants
# whose staff were seeded by earlier versions of this script. All idempotent.
#
# 1. The workshop auto-assign pool reads modryn_role_id.is_workshop; nothing
#    ever set it, so auto-assignment was dead on every seeded tenant.
seamstress.is_workshop = True
# 2. The seamstress needs the Workshop page in the role->page matrix, or she
#    cannot open /atelier at all. Guarded: _role_page_uniq makes a blind
#    create raise on a tenant where the owner already ticked it.
RolePage = env['modryn.role.page'].sudo()
if not RolePage.search_count([('role_id', '=', seamstress.id),
                              ('page_key', '=', 'atelier')]):
    RolePage.create({'role_id': seamstress.id, 'page_key': 'atelier'})
# 3. Roles were created with no language context, landing the Hebrew name under
#    the en_US jsonb key — Hebrew role names on /en pages, untranslatable.
#    update_field_translations sets each language directly, no source-lang
#    write-sync surprises.
for rec, en_name in ((sales, 'Sales'), (seamstress, 'Seamstress'),
                     (reception, 'Reception')):
    he_name = rec.with_context(lang='he_IL').name
    rec.update_field_translations('name', {'en_US': en_name, 'he_IL': he_name})

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
    # The public Railway demo tenant. Seed it with a ROTATED password, never
    # the shared dev one — this database ends up on a public URL.
    'te': [
        ('טל אבן',      sales,      'owner',   'tal',   '054-5550001'),
        ('עדן לוי',     sales,      'manager', 'eden',  '054-5550002'),
        ('גל שרון',     seamstress, 'staff',   'gal',   '054-5550003'),
    ],
    # The throwaway QA tenant — the one qa/README.md tells you to point the
    # browser suite at, because it carries modryn.twilio.disabled and because
    # write-tests mutate state that verify.sh then reads back off bella.
    # It was the ONLY slug the guard below refused, so the tenant existed and
    # could not be seeded: every @writes spec needs a login to sign in with.
    # Latin names on purpose — this is not a boutique and nothing here is shown
    # to a customer, so a failing assertion prints something legible.
    'qa': [
        ('QA Owner',    sales,      'owner',   'qaowner',   '050-5550001'),
        ('QA Manager',  sales,      'manager', 'qamanager', '050-5550002'),
        ('QA Seamstress', seamstress, 'staff', 'qastaff',   '050-5550003'),
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
                    'modryn_role_ids': [(6, 0, [job.id])],
                    'modryn_level': level,
                })
            else:
                employee = Employee.create({
                    'name': name,
                    'work_phone': phone,
                    'modryn_role_ids': [(6, 0, [job.id])],
                    'modryn_level': level,
                    'user_id': admin.id,
                })
            created.append((name, level, username, 'internal/admin'))
            continue

    employee = Employee.create({
        'name': name,
        'modryn_role_ids': [(6, 0, [job.id])],
        'modryn_level': level,
    })
    employee.modryn_provision_login(username, DEMO_PASSWORD)
    # AFTER provisioning, not in create(): provision relinks the work contact
    # to the new portal user's partner, dropping any phone written at create.
    # bella's and noga's original staff lost their numbers exactly this way.
    employee.work_phone = phone
    created.append((name, level, username, 'portal'))

env.cr.commit()

print('SEEDED_STAFF %s' % SLUG)
for row in created:
    print('  %s | %s | login=%s | %s' % row)
print('TOTAL_EMPLOYEES=%s' % Employee.search_count([]))
print('PORTAL_USERS=%s' % Users.search_count([('group_ids', 'in', env.ref('base.group_portal').ids)]))
