# Sweep the browser suite's leftovers off a playground tenant.
#
#   MODRYN_SLUG=qa ./odoo/odoo-bin shell -c odoo.conf -d qa \
#       --db-filter='^qa$' --no-http < scripts/tidy_playground.py
#
# WHY THIS EXISTS. Every browser run checks brides in, takes them, finishes some
# and abandons others, and files alteration work against them. None of that is
# cleaned up, by design - a spec that tidies after itself hides the state it left
# behind. After a few weeks the workshop column is eight hundred rows of "QA
# Alteration 4172", the floor board holds strangers, and the tenant stops being
# something a person can look at and judge.
#
# Two of those leftovers actively LIE, both learned the hard way:
#   - customers stuck in 'called' sit on the floor board forever, and any spec
#     reaching for .first() acts on one of them;
#   - OTP codes past the per-IP cap make check-in stop redirecting, which reads
#     as a broken check-in flow rather than as a rate limit doing its job.
#
# SEPARATE FROM THE SEEDER on purpose. Deleting is a different verb from
# stocking, and a script that quietly does both is one somebody runs expecting
# only the first.

import os

SLUG = os.environ.get('MODRYN_SLUG', '')

PROTECTED = ('bella', 'noga')
if SLUG in PROTECTED:
    raise SystemExit(
        "refusing to run on %r: verify.sh asserts that tenant's seeded state, "
        "and this deletes records. Use the qa tenant." % SLUG)

removed = {}


def sweep(label, records):
    count = len(records)
    if count:
        records.unlink()
        removed[label] = count


Task = env['modryn.alteration.task'].sudo().with_context(active_test=False)
sweep('alteration tasks', Task.search([
    '|', ('customer_name', 'like', 'QA Alteration%'),
         ('customer_name', 'like', 'QA Bride%')]))

Queue = env['modryn.queue.entry'].sudo()
# CLOSED tickets only. A waiting one is somebody the next run needs to find.
sweep('closed queue tickets', Queue.search(
    [('state', 'in', ('done', 'expired', 'redirected'))]))

# Held by nobody who is coming back. Set to done rather than deleted: the board's
# own counters read these rows, and a hole is worse than a closed ticket.
held = Queue.search([('state', '=', 'called')])
if held:
    removed['customers let go of'] = len(held)
    held.write({'state': 'done'})

Event = env['calendar.event'].sudo()
sweep('test bookings', Event.search([
    ('modryn_is_booking', '=', True), ('name', 'like', 'QA Bride%')]))

# The per-IP cap is thirty an hour and a full suite spends most of it. Clearing
# these is what stops the NEXT run reporting a broken check-in.
sweep('OTP codes', env['modryn.otp.code'].sudo().search([]))

# Accounts the roles act hires and archives. They are archived rather than
# deleted by the spec deliberately - that is the path the product offers - but
# they pile up one per run in the team box's "who has left" list.
Employee = env['hr.employee'].sudo().with_context(active_test=False)
probes = Employee.search([('name', 'like', 'Roles Probe%')])
if probes:
    users = probes.mapped('user_id')
    partners = users.mapped('partner_id')
    removed['probe accounts'] = len(probes)
    # The employee first: its user_id points at the user, so the user cannot go
    # while a row still names it.
    probes.unlink()
    users.unlink()
    partners.filtered(lambda p: not p.user_ids).unlink()

env.cr.commit()

print("")
print("  swept:")
for what in sorted(removed):
    print("     %-24s %s" % (what, removed[what]))
if not removed:
    print("     nothing to sweep")
print("")
