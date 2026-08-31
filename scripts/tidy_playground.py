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
# The suite's own brides, in any state it left them.
#
# NAMED, and that is the change that matters: this used to delete every closed
# ticket on the tenant. The playground now seeds finished visits deliberately -
# they are what the reports count and what the sales history reads on the
# walk-in side - so an unnamed sweep deleted the boutique's own past every time
# somebody tidied, which is a strange thing for a script about litter to do.
#
# WAITING ones go as well, and the comment here used to say the opposite: "a
# waiting one is somebody the next run needs to find". That is not true of
# these. Every spec mints its own bride with a timestamp in her name -
# `QA Walkin ${Date.now() % 100000}` - so a leftover waiting row is nobody's,
# and thirty of them are what the floor board leads with. The old caution still
# holds for rows this script cannot identify, which is why it identifies them.
SUITE = ('QA Walkin%', 'QA Bride%', 'QA Finish%')
_named = ['|'] * (len(SUITE) - 1) + [('name', 'like', p) for p in SUITE]
sweep('queue tickets the suite left', Queue.search(
    [('state', 'in', ('waiting', 'done', 'expired', 'redirected'))] + _named))

# Held by nobody who is coming back. Set to done rather than deleted: the board's
# own counters read these rows, and a hole is worse than a closed ticket.
#
# Named too, and for a sharper reason than the sweep above: the seeded floor puts
# two brides in somebody's hands ON PURPOSE, because the shift supervisor's
# "who has whom" panel is a heading over white space without them. A blanket
# release empties that panel every time this runs.
held = Queue.search([('state', '=', 'called')] + _named)
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
