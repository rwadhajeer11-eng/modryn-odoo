# A boutique with a past, so every screen has something real on it.
#
# The other seeders build a boutique that has just opened: staff, a few dresses,
# an empty diary. That is the right starting point for the demo and the wrong one
# for LOOKING at the product, because half these screens only say anything once
# the shop has been trading for a month. Reports on an empty diary is a page of
# zeroes; the workshop with no tasks is an empty column; hours worked with no
# attendance is "nothing recorded yet" for everybody.
#
# So this fills in a MONTH BEHIND: appointments that were kept, sold and missed,
# alteration work at every stage, shifts people actually stood, and a checklist
# the shop runs every morning.
#
#   MODRYN_SLUG=qa ./odoo/odoo-bin shell -c odoo.conf -d qa \
#       --db-filter='^qa$' --no-http < scripts/seed_playground.py
#
# IDEMPOTENT, and that is not a nicety here: it is meant to be run again after a
# browser suite has churned the tenant. Everything it makes is found by name or
# by date first, and topped up only if it is thin - so running it twice does not
# double the month's takings and make the reports lie.
#
# NEVER on bella or noga. Those two are asserted by verify.sh down to their
# opening-hours rows, and a seeder that adds a month of trading to them turns the
# gate red in a way that is very hard to tell from a real regression. The guard
# below refuses rather than trusting whoever typed the command.

import os
import random
from datetime import date, datetime, time, timedelta

SLUG = os.environ.get('MODRYN_SLUG', '')

PROTECTED = ('bella', 'noga')
if SLUG in PROTECTED:
    raise SystemExit(
        "refusing to run on %r: verify.sh asserts that tenant's seeded state "
        "down to its opening hours, and a month of invented trading there is "
        "indistinguishable from a regression. Use the qa tenant." % SLUG)

# One seed, so a second run against a fresh database produces the same shop.
# Anybody comparing two screenshots a week apart should be reading a change they
# made, not the dice.
random.seed(20260831)

Employee = env['hr.employee'].sudo()
Product = env['product.template'].sudo()
Event = env['calendar.event'].sudo()
Task = env['modryn.alteration.task'].sudo()
Piece = env['modryn.garment.piece'].sudo()
Attendance = env['modryn.shift.attendance'].sudo()
Checklist = env['modryn.task.template'].sudo()

TODAY = date.today()
made = {}


def note(what, n):
    made[what] = made.get(what, 0) + n


# --------------------------------------------------------------- the people
staff = Employee.search([('modryn_level', 'in', ('owner', 'manager', 'staff'))])
if not staff:
    raise SystemExit("no staff on this tenant - run scripts/seed_staff.py first")
sellers = staff.filtered(lambda e: e.modryn_level != 'owner') or staff

# --------------------------------------------------------------- the rail
# A rail worth looking at. The catalogue seeder ships three; a boutique with
# three dresses tells you nothing about how the picker behaves when a
# saleswoman types two letters and gets a list back.
GOWNS = [
    ("שמלת כלה אביגיל", 9800.0, {'36': 2, '38': 1, '40': 1}),
    ("שמלת כלה תמר", 11200.0, {'34': 1, '36': 2, '38': 0}),
    ("שמלת כלה שירה", 7400.0, {'36': 3, '38': 2, '40': 1}),
    ("שמלת כלה מיכל", 13900.0, {'36': 1, '38': 1}),
    ("שמלת ערב ליאור", 3800.0, {'34': 2, '36': 2, '38': 1}),
    ("שמלת ערב דנה", 4600.0, {'36': 1, '38': 3}),
    ("שמלת כלה רותם", 8200.0, {'38': 2, '40': 2}),
    ("שמלת ערב הילה", 5100.0, {'34': 1, '36': 1, '38': 1}),
]
size_attr = env['product.attribute'].sudo().search([('name', 'ilike', 'מידה')], limit=1)
for name, price, sizes in GOWNS:
    if Product.with_context(active_test=False).search_count([('name', '=', name)]):
        continue
    values = {
        'name': name,
        'type': 'consu',
        'is_storable': True,
        'list_price': price,
        'is_published': True,
    }
    if size_attr:
        values['attribute_line_ids'] = [(0, 0, {
            'attribute_id': size_attr.id,
            'value_ids': [(6, 0, [
                env['product.attribute.value'].sudo().search([
                    ('attribute_id', '=', size_attr.id), ('name', '=', s),
                ], limit=1).id or
                env['product.attribute.value'].sudo().create({
                    'attribute_id': size_attr.id, 'name': s,
                }).id for s in sizes
            ])],
        })]
    tmpl = Product.create(values)
    # Stock per size, so "sold out" is a real state on the finish picker and not
    # something only the code has ever seen.
    #
    # modryn_stock, NOT Odoo's warehouse quantity. The rail is the boutique's
    # own count on the variant - the owner types it into the Dresses screen and
    # every reader here (the screen, the finish picker, the storefront's
    # sold-out badge) asks that field. The first pass filled stock.quant
    # instead, which is Odoo's inventory and something nothing in this product
    # reads: qty_available came back 2/1/1 and every screen still said "this
    # size has gone".
    for variant in tmpl.product_variant_ids:
        label = variant.product_template_attribute_value_ids[:1].name or '36'
        variant.modryn_stock = sizes.get(label, 0)
    note('gowns', 1)

# --------------------------------------------------------- what the shop runs
MORNINGS = [
    ("לפתוח את התריסים ולהדליק את התאורה", 1),
    ("לבדוק שכל חדרי המדידה נקיים", 1),
    ("לעבור על התורים של היום", 1),
    ("לבדוק שהקיטור עובד", 2),
]
for label, order in MORNINGS:
    if not Checklist.with_context(active_test=False).search_count(
            [('name', '=', label)]):
        Checklist.create({'name': label, 'sequence': order})
        note('checklist items', 1)

# ------------------------------------------------------ a month of trading
# Appointments that already happened, with outcomes on them. This is what the
# reports screen reads: without closed visits carrying a price it can only ever
# show zero, which reads as the page being broken rather than as the shop being
# new.
OUTCOMES = (['sold'] * 5) + (['not_sold'] * 3) + ['no_show']
existing_past = Event.search_count([
    ('modryn_is_booking', '=', True),
    ('modryn_outcome', '!=', False),
])
if existing_past < 25:
    for days_ago in range(1, 29):
        day = TODAY - timedelta(days=days_ago)
        if day.weekday() == 4:          # Friday: the shop is shut
            continue
        for hour in (11, 14, 17):
            if random.random() > 0.55:
                continue
            start = datetime.combine(day, time(hour=hour - 3))   # local -> UTC
            if Event.search_count([
                    ('modryn_is_booking', '=', True), ('start', '=', start)]):
                continue
            who = random.choice(sellers)
            outcome = random.choice(OUTCOMES)
            event = Event.create({
                'name': random.choice([
                    "נועה כהן", "מאיה לוי", "שירה אברהם", "יעל מזרחי",
                    "תמר בן דוד", "רותם פרץ", "הילה שמש", "דנה אזולאי",
                ]),
                'start': start,
                'stop': start + timedelta(hours=1),
                'modryn_is_booking': True,
                'modryn_customer_phone': '+9725%08d' % random.randint(0, 99999999),
                'modryn_employee_id': who.id,
            })
            # WHICH gown, when one was sold. The sales history reads this - a
            # sale with a price and no dress answers "how much" and not "what",
            # which is half the question a bride asks three years later.
            gown = random.choice(GOWNS)[0] if outcome == 'sold' else ''
            event.write({
                'modryn_sale_items': gown,
                'modryn_outcome': outcome,
                'modryn_outcome_at': start + timedelta(hours=1),
                'modryn_outcome_by_id': who.id,
                'modryn_sale_amount': (
                    round(random.uniform(4000, 13000), -2) if outcome == 'sold' else 0.0),
                'modryn_visit_rating': random.choice([0, 3, 4, 4, 5, 5]),
            })
            note('past appointments', 1)

# -------------------------------------------------- a diary that points forward
# The block above is everything that already HAPPENED, because that is what the
# reports read. It leaves every screen about what happens NEXT empty: today's
# list, "coming later" on the floor, and the shift supervisor's arrival panel,
# which cannot say anything without a bride due inside the quarter hour.
#
# Nothing here carries an outcome. These have not happened yet, and writing one
# on a future appointment would put takings in the month's figures for a visit
# nobody has had - the reports would count a sale that has not been made.
BOOKED = [
    "אורית שרון", "ליאור דהן", "מיכל ברק", "עדי נחום", "שני גולן",
    "רוני אלמוג", "טל ביטון", "אביגיל רון", "נטע חדד", "סיון מור",
]
# The SHOP's date, not the machine's. The boutique runs three hours ahead of
# UTC, so for those three hours either side of midnight date.today() is still
# yesterday in the shop - and the dense day, the one that makes the arrival
# panel say anything, would be seeded entirely into the past.
local_today = (datetime.now() + timedelta(hours=3)).date()
ahead = Event.search_count([
    ('modryn_is_booking', '=', True),
    ('start', '>', datetime.now()),
    ('modryn_cancelled_at', '=', False),
])
if ahead < 20:
    for days_ahead in range(0, 14):
        day = local_today + timedelta(days=days_ahead)
        if day.weekday() == 4:          # Friday: the shop is shut
            continue
        # TODAY is seeded every half hour across the working day, and the rest
        # of the fortnight is seeded the way a boutique actually books - four or
        # five fittings, spread. The dense day is not decoration: the arrival
        # panel only says anything about the next fifteen minutes, so a diary
        # that jumps from eleven to two shows an empty panel for most of the
        # afternoon and reads as a feature that does not work.
        if days_ahead == 0:
            slots = [(h, m) for h in range(10, 19) for m in (0, 30)]
        else:
            slots = [(11, 0), (12, 30), (14, 0), (16, 0), (17, 30)]
            slots = random.sample(slots, random.randint(3, 5))
        for hour, minute in sorted(slots):
            start = datetime.combine(day, time(hour=hour - 3, minute=minute))
            # An hour that has already gone is not a booking to come. Seeding
            # one would leave a visit nobody can ever close, and the reports
            # count exactly those - "still to close" would climb by ten every
            # time this ran in the afternoon.
            if start <= datetime.now():
                continue
            if Event.search_count([
                    ('modryn_is_booking', '=', True), ('start', '=', start)]):
                continue
            Event.create({
                'name': random.choice(BOOKED),
                'start': start,
                'stop': start + timedelta(hours=1),
                'modryn_is_booking': True,
                'modryn_customer_phone': '+9725%08d' % random.randint(0, 99999999),
                # Some are spoken for and some are not, which is the state a
                # supervisor's screen is actually for: an unclaimed fitting is
                # the one she has to hand to somebody.
                'modryn_employee_id': random.choice(sellers).id
                if random.random() < 0.6 else False,
            })
            note('appointments to come', 1)

# ------------------------------------------------------------- the workshop
# One task in every state, so the columns are not three empty headings.
seamstress = staff.filtered(
    lambda e: any('תופרת' in (r.name or '') for r in e.modryn_role_ids))
seamstress = seamstress[:1] or sellers[:1]
pieces = Piece.search([], limit=3)
WORK = [
    ("נועה כהן", 'intake', '2', "לקצר 4 ס\"מ"),
    ("מאיה לוי", 'in_progress', '1', "להצר במותן"),
    ("שירה אברהם", 'ready', '1', "מוכן לאיסוף"),
    ("יעל מזרחי", 'delivered', '0', "נמסר"),
    ("תמר בן דוד", 'in_progress', '2', "דחוף — החתונה בשבת"),
]
for customer, state, priority, text in WORK:
    if Task.with_context(active_test=False).search_count(
            [('customer_name', '=', customer)]):
        continue
    Task.create({
        'customer_name': customer,
        'customer_phone': '+9725%08d' % random.randint(0, 99999999),
        'state': state,
        'priority': priority,
        'note': text,
        'due_date': TODAY + timedelta(days=random.randint(2, 20)),
        'seamstress_id': seamstress.id if seamstress else False,
        'piece_ids': [(6, 0, pieces[:2].ids)] if pieces else False,
    })
    note('alteration tasks', 1)

# --------------------------------------------------------- hours on the floor
# Four weeks of shifts for everybody, so "hours worked" has months to choose
# between and a total worth reading.
for employee in staff:
    for days_ago in range(1, 29):
        day = TODAY - timedelta(days=days_ago)
        if day.weekday() in (4, 5):
            continue
        if random.random() > 0.6:
            continue
        started = datetime.combine(day, time(hour=6))       # 09:00 local
        if Attendance.search_count([
                ('employee_id', '=', employee.id), ('started_at', '=', started)]):
            continue
        Attendance.create({
            'employee_id': employee.id,
            'started_at': started,
            'ended_at': started + timedelta(hours=random.choice([5, 6, 7, 8, 8, 9])),
        })
        note('shifts stood', 1)

env.cr.commit()

print("")
print("  the playground is stocked:")
for what in sorted(made):
    print("     %-22s %s" % (what, made[what]))
if not made:
    print("     nothing to add - it was already stocked")
print("")
