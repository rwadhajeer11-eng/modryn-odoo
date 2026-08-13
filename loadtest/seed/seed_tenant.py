# Seed ONE load-test tenant. Runs inside `odoo-bin shell`.
#
#   MODRYN_SLUG=lt07 MODRYN_TENANT_INDEX=07 MODRYN_DEMO_PASSWORD='...' \
#       ./odoo/odoo-bin shell -c odoo.conf -d lt07 \
#       --db-filter='^lt07$' --no-http < loadtest/seed/seed_tenant.py
#
# Replaces scripts/seed_staff.py for load tenants. That script is a dict keyed by
# slug with entries only for bella and noga, so PEOPLE['lt07'] raises KeyError and
# kills the shell mid-loop, leaving a half-seeded tenant behind. Everything here is
# derived from MODRYN_TENANT_INDEX instead, so any slug works.
#
# Idempotent: re-running skips anyone and anything that already exists.

import base64
import io
import os
import random
from datetime import datetime, timedelta

import pytz
from PIL import Image, ImageDraw

# Share the server's own definition of the grid rather than restating it.
# /book/submit refuses any time the server would not itself have offered, and
# verify.sh §"unoffered booking" asserts the same thing against the rows — so a
# seeded booking on a closed day, a closed hour or an off-the-hour minute is not
# merely unrealistic, it fails the suite. The weekdays and hours are no longer
# constants at all; they are read from modryn.opening.hours below. Only the
# fortnight, the timezone and the slot length still come from code.
from odoo.addons.modryn_booking.controllers.main import DAYS_AHEAD, TZ
from odoo.addons.modryn_booking.models.opening_hours import SLOT_MINUTES

SLUG = os.environ.get('MODRYN_SLUG')
if not SLUG:
    raise SystemExit("MODRYN_SLUG is unset.")

TENANT_INDEX = os.environ.get('MODRYN_TENANT_INDEX')
if TENANT_INDEX is None or not TENANT_INDEX.isdigit() or len(TENANT_INDEX) != 2:
    raise SystemExit(
        "MODRYN_TENANT_INDEX must be exactly two digits (00-99); got %r.\n"
        "It is the TT field of the +97252TTVVVV phone scheme, so a one-digit value\n"
        "would make tenant 7's VU 10042 collide with tenant 71's VU 0042."
        % (TENANT_INDEX,))

# No fallback, matching scripts/seed_staff.py: a default is how a credential gets
# re-committed. The harness reads the same value from its own environment.
DEMO_PASSWORD = os.environ.get('MODRYN_DEMO_PASSWORD')
if not DEMO_PASSWORD:
    raise SystemExit(
        "MODRYN_DEMO_PASSWORD is unset. Pick a password for the seeded logins, then:\n"
        "    MODRYN_DEMO_PASSWORD='pick-your-own' MODRYN_SLUG=%s MODRYN_TENANT_INDEX=%s"
        " ./odoo/odoo-bin shell -c odoo.conf -d %s --db-filter='^%s$' --no-http"
        " < loadtest/seed/seed_tenant.py" % (SLUG, TENANT_INDEX or 'NN', SLUG, SLUG))

MANAGERS = int(os.environ.get('MODRYN_MANAGERS', 3))
STAFF = int(os.environ.get('MODRYN_STAFF', 12))
# 40, not the 3 that scripts/seed_catalog.py gives the demo boutiques. The most
# expensive call under test is /floor/finish, which is a full board rebuild PLUS
# an unbounded `product.product` search over every published variant, whose whole
# result it serialises into the response (modryn_staff/controllers/floor.py). At
# 3 dresses that search returns 12 rows and the variant list is noise next to the
# board — the call looks cheap because the fixture is trivial, not because the
# server is fast, and the campaign's headline claim about it measures nothing.
#
# 40 templates x the 4 sizes below = 160 variants, which is a boutique-scale rail
# (bella and noga carry 3 and 2 published templates because they are demos) and
# makes the variant list the dominant term in the payload. Basis for the number
# is the call, not market research: 160 rows is where the search stops
# disappearing into the board's own cost. Raise it with MODRYN_DRESSES if a stage
# needs a bigger catalog. Measured cost on this host (image resize plus one stock
# quant per in-stock size): the seed step goes 4.6 s at 3 dresses to 10.8 s at
# 40, i.e. ~0.17 s each, taking a tenant end to end from ~9 s to ~15 s.
DRESSES = int(os.environ.get('MODRYN_DRESSES', 40))
BOOKINGS = int(os.environ.get('MODRYN_BOOKINGS', 20))
QUEUE = int(os.environ.get('MODRYN_QUEUE', 8))

# Deterministic, so a re-seeded tenant produces the SAME people, dresses and
# customer phones. A stage that restarts must not collide with the previous
# stage's modryn_otp_code rows: MAX_SENDS_PER_HOUR is 3 per phone per rolling
# hour, and a re-randomised scheme would look like a rate-limit wall instead.
random.seed('modryn-loadtest-%s' % TENANT_INDEX)


def phone_for(vu_index):
    """+972 52 TT VVVV — see load-test-plan §3. Passes normalize_il_phone AND
    modryn_booking's PHONE_RE, both checked in the plan.

    Well-formed but NOT deliverable: 11 digits, where a real Israeli mobile in
    E.164 is 12 (+972 5X + seven subscriber digits). See assert_phone_scheme.
    """
    return '+97252%s%04d' % (TENANT_INDEX, vu_index)


def assert_phone_scheme():
    """Fail loudly if the seeded numbers ever become deliverable.

    The repo used to say in four places that these are "real Israeli mobile
    prefixes" that would "text strangers". They are not. `+97252TTVVVV` is 12
    characters — a plus and ELEVEN digits — while a reachable Israeli mobile is
    `+972` + `5X` + seven subscriber digits = twelve. Twilio answers a number
    that short with a 21211 (invalid 'To'), unbilled and undelivered. The SQL in
    reset_tenants.sh agrees: it renumbers `where length(phone) = 12`.

    That overstatement was not harmless. It is exactly the reasoning that lets a
    reviewer look at a weak guard, decide the consequence is already terrifying
    enough, and leave the guard where it is — which is how the Twilio check in
    both seeder scripts spent its whole life running AFTER the destruction it
    was supposed to prevent.

    So: pin the fact rather than the prose. Lengthening the scheme to a real
    number is a legitimate change — it would make the fixture more faithful —
    but it must not happen quietly, because from that moment the modryn.twilio.*
    checks really are the last thing before the phone bill, and the harness's
    own `phonePrefix + 4 digits` (k6/lib/session.js) has to move with it.
    """
    sample = phone_for(0)
    digits = sum(c.isdigit() for c in sample)
    if digits != 11:
        raise SystemExit(
            "phone_for() now yields %r — %d digits, not the 11 this fixture is\n"
            "documented and asserted to produce.\n"
            "If that was deliberate and these are now REACHABLE handsets:\n"
            "  1. update k6/lib/session.js iterationPhone() to match, or the\n"
            "     harness books for numbers no VU owns;\n"
            "  2. update the `length(phone) = 12` renumbering SQL in\n"
            "     reset_tenants.sh, which silently matches nothing otherwise;\n"
            "  3. treat the modryn.twilio.* checks in gen_tenants.sh and\n"
            "     reset_tenants.sh as delivery-critical, not merely hygiene."
            % (sample, digits))


assert_phone_scheme()


# --------------------------------------------------------------------- people
Employee = env['hr.employee'].sudo()
Role = env['modryn.staff.role'].sudo()


def role(name):
    return Role.search([('name', '=', name)], limit=1) or Role.create({'name': name})


sales = role('מוכרת')
seamstress = role('תופרת')
reception = role('קבלת קהל')

# Usernames repeat across tenants on purpose. res.users.login is unique PER
# DATABASE and one database per boutique is the tenancy model, so every tenant
# can share one credential shape and the k6 scenarios stay free of per-tenant
# special cases.
PEOPLE = [('בעלת הבית %s' % TENANT_INDEX, sales, 'owner', 'owner')]
PEOPLE += [('מנהלת משמרת %s-%d' % (TENANT_INDEX, n), sales, 'manager', 'mgr%d' % n)
           for n in range(1, MANAGERS + 1)]
PEOPLE += [('מוכרת %s-%02d' % (TENANT_INDEX, n),
            (seamstress if n % 4 == 0 else reception if n % 3 == 0 else sales),
            'staff', 'staff%02d' % n)
           for n in range(1, STAFF + 1)]

# The k6 staff scenario signs in as plain staff and asserts the floor board
# renders — under the matrix's default grants (roster + check-in) it would see
# the themed refusal instead. This tenant models a boutique whose owner
# granted the floor to every role, which keeps the scenario honest without
# special-casing it.
Grant = env['modryn.role.page'].sudo()
for job in (sales, seamstress, reception):
    if not Grant.search_count([('role_id', '=', job.id), ('page_key', '=', 'floor')]):
        Grant.create({'role_id': job.id, 'page_key': 'floor'})

people_made = 0
for name, job, level, username in PEOPLE:
    if Employee.with_context(active_test=False).search_count([('name', '=', name)]):
        continue

    if level == 'owner':
        # Reuse the database's existing admin rather than adding a second
        # internal (billable) seat. Installing `hr` already created an employee
        # for admin and Odoo enforces one employee per (user, company), so adopt
        # that row — creating a second raises hr_employee_user_uniq.
        admin = env.ref('base.user_admin', raise_if_not_found=False)
        if admin:
            admin.write({
                'login': username,
                'password': DEMO_PASSWORD,
                'group_ids': [(4, env.ref('modryn_staff.group_boutique_owner').id)],
            })
            employee = Employee.with_context(active_test=False).search(
                [('user_id', '=', admin.id)], limit=1)
            values = {'name': name, 'work_phone': '03-555%s00' % TENANT_INDEX,
                      'modryn_role_id': job.id, 'modryn_level': level}
            if employee:
                employee.write(values)
            else:
                Employee.create(dict(values, user_id=admin.id))
            people_made += 1
            continue

    employee = Employee.create({
        'name': name,
        'work_phone': '052-555%s%02d' % (TENANT_INDEX, people_made),
        'modryn_role_id': job.id,
        'modryn_level': level,
    })
    employee.modryn_provision_login(username, DEMO_PASSWORD)
    people_made += 1

# --------------------------------------------------------------------- rooms
Room = env['modryn.fitting.room'].sudo()
for n in range(1, 4):
    label = 'תא %d' % n
    if not Room.with_context(active_test=False).search_count([('name', '=', label)]):
        Room.create({'name': label, 'sequence': n * 10})

Piece = env['modryn.garment.piece'].sudo()
for n, label in enumerate(('שובל', 'מחוך', 'שרוולים'), start=1):
    if not Piece.with_context(active_test=False).search_count([('name', '=', label)]):
        Piece.create({'name': label, 'sequence': n * 10})

# ------------------------------------------------------------------- catalog
# Placeholder art in the brand palette, same idea as scripts/seed_catalog.py: the
# storefront pages under test must serve a real image, because image delivery is
# part of what /shop costs. No gallery images — the visitor journey pages through
# the grid, not through one dress's carousel.
CREAM, SURFACE, GOLD, INK = (253, 251, 247), (246, 240, 230), (197, 160, 89), (43, 33, 24)


def swatch(seed, w=900, h=1200):
    img = Image.new('RGB', (w, h), CREAM if seed % 2 == 0 else SURFACE)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, w - 40, h - 40], outline=GOLD, width=3)
    cx, cy = w // 2, h // 2
    d.polygon([(cx, cy - 320), (cx + 190, cy + 300), (cx - 190, cy + 300)],
              outline=GOLD, width=3)
    d.ellipse([cx - 34, cy - 380, cx + 34, cy - 312], outline=INK, width=3)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    return base64.b64encode(buf.getvalue())


Attribute = env['product.attribute'].sudo()
size_attr = Attribute.search([('name', '=', 'מידה')], limit=1) or Attribute.create({
    'name': 'מידה', 'display_type': 'pills', 'create_variant': 'always',
})
Value = env['product.attribute.value'].sudo()


def size_value(code):
    return (Value.search([('attribute_id', '=', size_attr.id), ('name', '=', code)], limit=1)
            or Value.create({'attribute_id': size_attr.id, 'name': code}))


Template = env['product.template'].sudo()
stock_location = env['stock.warehouse'].sudo().search([], limit=1).lot_stock_id
SIZES = ('36', '38', '40', '42')

for idx in range(DRESSES):
    name = 'שמלה %s-%02d' % (TENANT_INDEX, idx + 1)
    if Template.search_count([('name', '=', name)]):
        continue
    values = [size_value(code) for code in SIZES]
    tmpl = Template.create({
        'name': name,
        'list_price': 3500.0 + idx * 1700,
        'type': 'consu',
        'is_storable': True,
        'is_published': True,
        'modryn_price_visible': idx % 3 != 2,
        'image_1920': swatch(idx),
        'attribute_line_ids': [(0, 0, {
            'attribute_id': size_attr.id,
            'value_ids': [(6, 0, [v.id for v in values])],
        })],
    })
    # A zero quantity is what makes the storefront show a size as unavailable, so
    # leave one size empty per dress — the visitor journey should meet both cases.
    for n, variant in enumerate(tmpl.product_variant_ids):
        if n == idx % len(SIZES):
            continue
        env['stock.quant'].sudo().with_context(inventory_mode=True).create({
            'product_id': variant.id,
            'location_id': stock_location.id,
            'inventory_quantity': 2,
        }).action_apply_inventory()

dress_ids = Template.search([('is_published', '=', True)], order='id').ids

# ------------------------------------------------------------------ bookings
# Only the hours /book would itself offer, read from modryn.opening.hours — the
# same model _slots() reads, so the two cannot drift. Jerusalem local, derived by
# localising a local wall clock the way _slots() does. NOT by UTC
# arithmetic — Israel observes DST, so a fixed offset drifts by an hour for half
# the year and would put every seeded booking one hour off the grid in summer.
now_local = datetime.now(TZ)
open_days = []          # [(date, [utc slot, ...]), ...] in calendar order
# From offset 2, not 1. Tomorrow's slots belong to the booking scenario, and a
# seed run that straddles local midnight would see offset 1 become "today" — a
# day /book never offers — leaving live bookings the server would now refuse.
# The boutique's own week, from the same model the controllers read, in one
# query. The grid used to be OPEN_WEEKDAYS/OPEN_HOUR/CLOSE_HOUR imported from
# the controller; those became data, and an empty hour list is now how a closed
# day is spelt.
by_weekday = env['modryn.opening.hours'].sudo().modryn_hours_by_weekday()
for offset in range(2, DAYS_AHEAD + 1):
    day = (now_local + timedelta(days=offset)).date()
    # {hour: capacity} now, and iterating it yields the hours — which is all this
    # loop wants. The seats are ignored on purpose; see the FULL_DAYS note below.
    hours = by_weekday.get(str(day.weekday()), {})
    if not hours:
        continue
    slots = []
    for hour in hours:
        whole = int(hour)
        naive = datetime.combine(day, datetime.min.time()).replace(
            hour=whole, minute=int(round((hour - whole) * 60)))
        slots.append(TZ.localize(naive).astimezone(pytz.utc).replace(tzinfo=None))
    open_days.append((day, slots))

# Reported, not assumed: with owner-defined hours a boutique's days need not be
# the same length as each other.
SLOTS_PER_DAY = max((len(slots) for _day, slots in open_days), default=0)
grid = [slot for _day, slots in open_days for slot in slots]

# ---------------------------------------------------------------- full days
# WHY this parameter exists at all: /book renders the waitlist form ONLY for days
# where every hour is taken (`full = not times` in modryn_booking's _slots, and
# `t-if="full_days"` in its template). The visitor journey's waitlist branch is
# 15% of that journey and ~13% of total workload, and it opens by reading the
# day <select> out of /book — no options, no POST, no assertion recorded. So a
# seed that never fills a day does not merely under-exercise the waitlist: it
# deletes it from the run, and the k6 summary shows nothing missing because the
# checks were never reached.
#
# Uniform sampling would not produce one. The grid holds 72-80 slots (see the
# arithmetic printed at the end), the default is 20 bookings, and filling any
# specific day means drawing all 8 of its hours: C(64,12)/C(72,20) = 1.05e-5,
# so 9.5e-5 across nine days. And it is not even a per-run dice roll — the seed
# is `random.seed('modryn-loadtest-<index>')`, so a tenant index either draws a
# full day or never does, for the life of the fixture. Worse, the guard below
# USED to raise whenever BOOKINGS reached the grid size, and README told
# operators to keep it "well under" it. The seed guaranteed the branch was dead.
#
# So fill whole days on purpose. They are ordinary grid slots — same open
# weekday, same 10:00-17:00 hours, same exact hour — so scripts/verify.sh
# §"unoffered booking" still passes: every row is one the server itself would
# have offered.
#
# ponytail: one booking per slot fills a day only while every window seats one,
# which is the default and what every fixture ships with. Raise a window's
# capacity on a load tenant and these days stop being full — /book renders a time
# picker instead of the waitlist form and the branch silently leaves the run
# again. Fix when a fixture needs capacity: book each full-day slot `capacity`
# times, on seats 0..capacity-1.
FULL_DAYS = int(os.environ.get('MODRYN_FULL_DAYS', 1))

if FULL_DAYS < 0:
    raise SystemExit("MODRYN_FULL_DAYS must be >= 0; got %s." % FULL_DAYS)
if FULL_DAYS >= len(open_days):
    raise SystemExit(
        "MODRYN_FULL_DAYS=%s would fill %s of the %s open days in the seed window; "
        "at least one day must keep a free hour or /book offers nothing and every "
        "booking POST answers 'just taken'." % (FULL_DAYS, FULL_DAYS, len(open_days)))

# The LAST open days, not the first. Both render — the waitlist <select> lists
# every full day in the fortnight — but the window slides forward with the clock
# while the gold does not. A day seeded at offset 2 is "today" two days later, a
# day /book never offers, and verify.sh then reports the seed's own rows as
# unoffered bookings. Taking from the far end buys the gold ~12 days of shelf
# life instead of ~1.
full_days = open_days[len(open_days) - FULL_DAYS:] if FULL_DAYS else []
full_slots = [slot for _day, slots in full_days for slot in slots]
taken_by_full = set(full_slots)
free_grid = [slot for slot in grid if slot not in taken_by_full]

if BOOKINGS >= len(free_grid):
    raise SystemExit(
        "MODRYN_BOOKINGS=%s does not fit: the %s-slot grid has %s slot(s) left "
        "after MODRYN_FULL_DAYS=%s takes %s. The booking journey would have "
        "nothing to book and every POST would answer 'just taken'. Lower "
        "MODRYN_BOOKINGS or MODRYN_FULL_DAYS."
        % (BOOKINGS, len(grid), len(free_grid), FULL_DAYS, len(full_slots)))

Event = env['calendar.event'].sudo()
Partner = env['res.partner'].sudo()
organizer = env.ref('base.user_admin', raise_if_not_found=False)
Variant = env['product.product'].sudo()

# Spread across the fortnight rather than filling the first days: a board that is
# solid on Sunday and empty on Thursday measures a different query shape. Sampled
# from free_grid, so the deliberately-full days above stay full instead of being
# double-counted against BOOKINGS.
chosen = full_slots + random.sample(free_grid, BOOKINGS)
bookings_made = 0
for n, start in enumerate(sorted(chosen)):
    # The unique index is on (start, seat) for live bookings, and this seeder only
    # ever writes seat 0, so a re-run must not try the same hour twice — and an
    # hour a load run already booked is equally off limits. Conservative once a
    # window seats more than one: it skips an hour that still has room, which
    # costs a booking and never a collision.
    if Event.search_count([('modryn_is_booking', '=', True), ('start', '=', start),
                           ('modryn_cancelled_at', '=', False)]):
        continue
    phone = phone_for(n)
    partner = Partner.search([('phone', '=', phone)], limit=1) or Partner.create({
        'name': 'לקוחה %s-%04d' % (TENANT_INDEX, n), 'phone': phone,
    })
    variant = Variant.search([('product_tmpl_id', 'in', dress_ids)], limit=1, offset=n % 4)
    Event.create({
        'name': 'Fitting %s-%04d' % (TENANT_INDEX, n),
        'start': start,
        'stop': start + timedelta(minutes=SLOT_MINUTES),
        'modryn_is_booking': True,
        'modryn_booking_type': 'dress' if variant else 'consult',
        'modryn_variant_id': variant.id if variant else False,
        'modryn_customer_phone': phone,
        'modryn_terms_accepted_at': datetime.utcnow(),
        'partner_ids': [(6, 0, partner.ids)],
        # Named explicitly: sudo() elevates privileges but leaves env.user alone,
        # so calendar.event's user_id default would resolve to whoever runs the
        # shell rather than the boutique's calendar owner.
        **({'user_id': organizer.id} if organizer else {}),
    })
    bookings_made += 1

# --------------------------------------------------------------------- queue
# A floor board with an empty queue renders a different (and much cheaper) page
# than a real one, so every stage starts from the same non-trivial baseline.
Entry = env['modryn.queue.entry'].sudo()
open_domain = [('state', 'in', ('pending', 'waiting', 'called'))]
open_now = Entry.search_count(open_domain)
# One open place per number is now a Postgres index, and these phones are
# deterministic — a k6 run that opened a walk-in in this 4-digit space, or a
# previous seed whose entries partially closed, would make a blind create()
# crash the whole seed. Skip any number that already holds an open place.
open_phones = set(Entry.search(open_domain).mapped('phone'))
n, created = 0, 0
while open_now + created < QUEUE and n < 10 * QUEUE:
    phone = phone_for(9000 + n)
    n += 1
    if phone in open_phones:
        continue
    Entry.create({
        'name': 'ממתינה %s-%02d' % (TENANT_INDEX, n - 1),
        'phone': phone,
        'client_type': 'bride' if (n - 1) % 3 else 'evening',
        'state': 'waiting' if (open_now + created) else 'called',
    })
    created += 1

# odoo-bin shell does NOT commit on exit. Without this the whole seed vanishes
# silently and the tenant looks like a clone that was never touched.
env.cr.commit()

print('SEEDED_TENANT %s index=%s' % (SLUG, TENANT_INDEX))
print('  employees=%s' % Employee.search_count([]))
print('  dresses=%s ids=%s' % (len(dress_ids), dress_ids))
print('  bookings_created=%s live=%s' % (
    bookings_made,
    Event.search_count([('modryn_is_booking', '=', True), ('modryn_cancelled_at', '=', False)])))
# The waitlist branch of the visitor journey renders only when this is non-zero,
# so print it where an operator reading the seed log will see it go to 0.
print('  grid=%s slots (%s open day(s) x %s hours, offsets 2..%s)'
      % (len(grid), len(open_days), SLOTS_PER_DAY, DAYS_AHEAD))
print('  full_days=%s (%s) -> %s waitlist slot(s); %s free slot(s), %s sampled'
      % (FULL_DAYS,
         ', '.join(str(d) for d, _s in full_days) or 'none',
         len(full_slots), len(free_grid), BOOKINGS))
print('  queue_open=%s' % Entry.search_count([('state', 'in', ('pending', 'waiting', 'called'))]))
print('  phone_scheme=%s..%s' % (phone_for(0), phone_for(9999)))
