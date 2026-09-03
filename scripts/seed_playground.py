# -*- coding: utf-8 -*-
"""Fill the playground so every screen has something on it.

WHAT THIS IS FOR. `qa` is the tenant meant to be clicked around, and half its
screens were empty — not broken, just never filled in: no kinds of dress worth
filtering by, nobody allowed to book, no booking hours, no discount codes, no
fitting rooms, no announcement, one sale. A screen with nothing on it teaches
nothing about whether it works.

NOT bella AND NOT noga. verify.sh asserts their seeded state down to the
opening-hours rows, and adding a row to either turns the gate red in a way that
looks like a regression and is not. This script refuses to run anywhere else.

IDEMPOTENT. Every block looks before it writes, so running it twice adds
nothing and running it after a change tops up only what is missing.

    ./odoo/odoo-bin shell -c odoo.conf -d qa --no-http < scripts/seed_playground.py
"""
import datetime

DB = env.cr.dbname
assert DB == 'qa', (
    "This fills a playground and only a playground. bella and noga are asserted "
    "by verify.sh; %r is not qa." % DB)


def say(what, count):
    print('  %-32s %s' % (what, count))


# ============================================================ kinds of dress
# Three languages on each, because the filter on /shop prints these to a bride
# and the boutique's own list is the only place those words exist.
KINDS = [
    ('Princess', 'נסיכה', 'أميرة', False),
    ('Mermaid', 'מרמייד', 'حورية', False),
    ('A-line', 'גזרת A', 'قصة A', False),
    ('Simple', 'עדינה', 'بسيطة', False),
    ('Modest', 'צנועה', 'محتشمة', False),
    ('Evening', 'ערב', 'سهرة', False),
    ('Veil', 'הינומה', 'طرحة', True),
    ('Jewellery', 'תכשיט', 'إكسسوار', True),
]
Kind = env['modryn.dress.type']
made = 0
for en, he, ar, accessory in KINDS:
    kind = Kind.with_context(active_test=False).search(
        ['|', ('name', '=ilike', en), ('name', '=ilike', he)], limit=1)
    if not kind:
        kind = Kind.create({'name': en, 'is_accessory': accessory,
                            'sequence': 10 + KINDS.index((en, he, ar, accessory))})
        made += 1
    # ALL THREE, even for a kind that already existed. The two qa started with
    # were typed in Hebrew and had Hebrew sitting in the en_US slot as well —
    # so an Arabic-reading bride read Hebrew, and the round-robin below, which
    # looks kinds up by their English name, could not find them at all.
    kind.with_context(lang='en_US').name = en
    kind.with_context(lang='he_IL').name = he
    kind.with_context(lang='ar_001').name = ar
say('kinds of dress (new)', made)

# Every published thing gets one. A rail where five of thirteen can be filtered
# is a filter that looks broken.
Template = env['product.template']
by_word = {}
for kind in Kind.search([]):
    by_word[kind.with_context(lang='en_US').name.lower()] = kind
GUESS = [
    ('veil', 'veil'), ('הינומה', 'veil'),
    ('belt', 'jewellery'), ('חגורת', 'jewellery'), ('סרט', 'jewellery'),
    ('glove', 'jewellery'), ('כפפות', 'jewellery'),
    ('ערב', 'evening'),
]
# The bridal cuts, in turn. A rail where every gown is a princess makes the
# filter look broken the first time somebody tries it: one tick and nothing
# changes. Spreading them means every kind on the list finds something.
CUTS = ['princess', 'mermaid', 'a-line', 'simple', 'modest']
placed = 0
turn = 0
for tmpl in Template.search([('is_published', '=', True),
                             ('modryn_type_id', '=', False)], order='id'):
    name = (tmpl.name or '').lower()
    word = next((k for needle, k in GUESS if needle in name), None)
    if word is None:
        word = CUTS[turn % len(CUTS)]
        turn += 1
    kind = by_word.get(word)
    if kind:
        tmpl.modryn_type_id = kind.id
        placed += 1
say('dresses given a kind', placed)

# The old seed called every accessory a veil, so the jewellery kind had nothing
# and the filter hid it - a kind on the boutique's list that a bride can never
# see is worse than not having it.
veil = by_word.get('veil')
jewellery = by_word.get('jewellery')
moved = 0
if veil and jewellery:
    for tmpl in Template.search([('modryn_type_id', '=', veil.id)]):
        name = (tmpl.name or '')
        if any(word in name for word in ('חגורת', 'סרט', 'כפפות', 'belt',
                                         'ribbon', 'glove')):
            tmpl.modryn_type_id = jewellery.id
            moved += 1
say('accessories moved off the veil', moved)

# ============================================================= who may book
VISITORS = [
    ('A bride', 'כלה', 'عروس', 'The one wearing it'),
    ("A bride's sister", 'אחות של כלה', 'أخت العروس', ''),
    ('Mother of the bride', 'אמא של הכלה', 'أم العروس', ''),
    ('An evening dress', 'שמלת ערב', 'فستان سهرة', 'Not a wedding'),
]
Visitor = env['modryn.customer.kind']
made = 0
for index, (en, he, ar, note) in enumerate(VISITORS):
    who = Visitor.with_context(active_test=False).search(
        ['|', ('name', '=ilike', en), ('name', '=ilike', he)], limit=1)
    if not who:
        who = Visitor.create({'name': en, 'note': note,
                              'sequence': 10 + index * 10})
        made += 1
    who.with_context(lang='he_IL').name = he
    who.with_context(lang='ar_001').name = ar
say('who may book (new)', made)

# =========================================================== booking hours
# A week a real boutique could have: ordinary days two an hour, Thursday
# evening busier, Friday morning only, Saturday shut. Written only when the
# grid is empty, so a week set by hand is never overwritten.
Queue = env['modryn.queue.hour']
if not Queue.search_count([]):
    # ONE SEAT AN HOUR, and the shape carries the interest instead: which
    # hours are open on which day, Friday morning, Thursday evening, an hour
    # shut at two.
    #
    # NOT because one is more realistic. Because the browser suite's act 3c
    # books a slot and asserts the grid stops offering that hour, which is only
    # true at one seat — at two it books one, one is left, the hour correctly
    # stays, and a correct product fails a correct check because the fixture
    # under both of them moved. verify.sh pins the old capacity column at 1 for
    # this exact reason and cannot see the grid that replaced it.
    #
    # Two at eleven is five seconds of typing on the screen. It is not a thing
    # a fixture should decide on behalf of a gate.
    WEEK = {
        '6': [(10, 1), (11, 1), (12, 1), (13, 1), (15, 1), (16, 1), (17, 1)],
        '0': [(10, 1), (11, 1), (12, 1), (13, 1), (15, 1), (16, 1), (17, 1)],
        '1': [(10, 1), (11, 1), (12, 1), (13, 1), (15, 1), (16, 1), (17, 1)],
        '2': [(10, 1), (11, 1), (12, 1), (13, 1), (15, 1), (16, 1), (17, 1)],
        '3': [(10, 1), (11, 1), (12, 1), (13, 1), (15, 1), (16, 1),
              (17, 1), (18, 1), (19, 1), (20, 1)],
        '4': [(9, 1), (10, 1), (11, 1), (12, 1)],
    }
    rows = 0
    for weekday, hours in WEEK.items():
        for hour, how_many in hours:
            Queue.create({'weekday': weekday, 'hour': float(hour),
                          'how_many': how_many})
            rows += 1
    say('booking-hour rows', rows)
else:
    say('booking-hour rows (kept)', Queue.search_count([]))

# Friday morning has to be OPEN as well, or the front page says shut on a day
# the booking grid sells.
Hours = env['modryn.opening.hours']
if not Hours.with_context(active_test=False).search_count([('weekday', '=', '4')]):
    Hours.create({'weekday': '4', 'start_hour': 9.0, 'end_hour': 13.0})
    say('Friday opening window', 'added')

# ============================================================ discount codes
CODES = [
    ('BRIDE10', 10, 'The autumn fair'),
    ('SISTER5', 5, 'A sister of a bride we dressed'),
    ('STAFF20', 20, 'Family and staff'),
]
Code = env['modryn.discount.code']
made = 0
for word, percent, note in CODES:
    if not Code.with_context(active_test=False).search_count([('code', '=ilike', word)]):
        Code.create({'code': word, 'percent': percent, 'note': note})
        made += 1
say('discount codes (new)', made)

# ============================================================= fitting rooms
ROOMS = [('Room 1', 'חדר 1'), ('Room 2', 'חדר 2'), ('The big room', 'החדר הגדול')]
Room = env['modryn.fitting.room']
made = 0
for index, (en, he) in enumerate(ROOMS):
    if not Room.with_context(active_test=False).search_count(
            ['|', ('name', '=ilike', en), ('name', '=ilike', he)]):
        Room.create({'name': he, 'sequence': 10 + index * 10})
        made += 1
say('fitting rooms (new)', made)

# ============================================================== a closure
Closure = env['modryn.closure']
if not Closure.with_context(active_test=False).search_count([]):
    first = datetime.date.today() + datetime.timedelta(days=21)
    Closure.create({'name': 'ראש השנה', 'date_from': first,
                    'date_to': first + datetime.timedelta(days=1),
                    'full_day': True})
    say('closure', 'one, three weeks out')

# ============================================================ an announcement
Announcement = env['modryn.announcement']
if not Announcement.search_count([]):
    author = env['hr.employee'].search([('name', 'ilike', 'owner')], limit=1) \
        or env['hr.employee'].search([], limit=1)
    Announcement.create({
        'body': 'שישי הקרוב פתוחות עד 13:00. מי שיכולה להישאר לסידור הרף '
                'אחרי הסגירה — תודיע לי.',
        'author_id': author.id if author else False,
        'author_name': author.name if author else '',
    })
    say('announcement', 'one')

# ================================================================== sales
# The owner's "dresses sold" screen searches by name, and her reports count
# months. One sale answers neither. Written only when there is almost nothing,
# so a real till session is never buried under invented ones.
Sale = env['modryn.sale']
if Sale.search_count([]) < 4:
    import random

    seller = env['hr.employee'].search([], limit=1)
    # GOWNS, not the first twenty variants on the rail. Sorted by price, the
    # first twenty are belts and gloves, and a month's takings that read 240
    # shekels teaches nothing about the report that prints them.
    variants = env['product.product'].search(
        [('product_tmpl_id.is_published', '=', True),
         ('product_tmpl_id.modryn_is_accessory', '=', False)],
        order='list_price desc', limit=20)
    BRIDES = [
        ('מירב כהן', '050-1112233'),
        ('נור אבו-חסן', '052-2223344'),
        ('שירה לוי', '054-3334455'),
        ('רים חדאד', '053-4445566'),
    ]
    made = 0
    for index, (name, phone) in enumerate(BRIDES):
        if not variants:
            break
        variant = variants[index % len(variants)]
        price = variant.list_price or 4200.0
        values = {
            'customer_name': name,
            'customer_phone': phone,
            'employee_id': seller.id if seller else False,
            'line_ids': [(0, 0, {
                'variant_id': variant.id,
                'description': variant.product_tmpl_id.name,
                'price': price,
            })],
        }
        # One of the four bought with a code, one with a hand-typed discount,
        # two at full price - so the tracking screen has all three shapes on it.
        if index == 0:
            values.update(discount_kind='percent', discount_value=10.0,
                          discount_reason='BRIDE10')
        elif index == 1:
            values.update(discount_kind='amount', discount_value=300.0,
                          discount_reason='אחות של כלה שקנתה אצלנו')
        if index == 2:
            values.update(altered=True,
                          alteration_note='לקצר את השובל ולהצר במותן')
        Sale.create(values)
        made += 1
    say('sales written', made)

# ===================================================== the three empty screens
#
# NONE OF THESE WAS SHORT OF DATA. qa had 111 bookings, 38 closed ones and 65
# shift slots, and all three screens still read as blank — because each of them
# asks a narrower question than "is there any data":
#
#   the main screen  asks about TODAY and about the person signed in
#   the reports      ask about THIS MONTH
#   the shifts       ask about NEXT WEEK, published
#
# and the seed data answered none of those. Filling a table is not the same as
# filling a screen.

import datetime as _dt

TODAY = _dt.date.today()
Employee = env['hr.employee']
# The three real ones. The "Roles Probe" rows are verify.sh's own litter and
# putting a bride's fitting on one of them would be a lie on a screen.
staff = Employee.search([('name', 'in', ['QA Owner', 'QA Manager',
                                         'QA Seamstress'])], order='id')
Event = env['calendar.event'].sudo()

# ------------------------------------------------- the main screen: today
# Every one of today's bookings gets a stylist, in turn, so whoever signs in
# has something on her own screen rather than one of the three having all of
# it and the other two an empty page.
if staff:
    today_start = _dt.datetime.combine(TODAY, _dt.time.min)
    today_end = _dt.datetime.combine(TODAY, _dt.time.max)
    todays = Event.search([('modryn_is_booking', '=', True),
                           ('start', '>=', today_start),
                           ('start', '<=', today_end)], order='start')
    for index, event in enumerate(todays):
        event.modryn_employee_id = staff[index % len(staff)].id
    say("today's bookings given a stylist", len(todays))

    # And a couple of walk-ins waiting for the owner, which is the other half
    # of that screen.
    Queue = env['modryn.queue.entry'].sudo()
    waiting = Queue.search([('state', '=', 'waiting'),
                            ('modryn_employee_id', '=', False)], limit=3)
    for index, entry in enumerate(waiting):
        entry.modryn_employee_id = staff[index % len(staff)].id
    say('walk-ins given a stylist', len(waiting))

# --------------------------------------------------- the reports: this month
# Everything closed in qa was closed in AUGUST, and the report opens on the
# current month — so a screen with a month of real numbers behind it showed
# zeroes. Past bookings in THIS month get an outcome.
month_start = _dt.datetime.combine(TODAY.replace(day=1), _dt.time.min)
now = _dt.datetime.now()
unclosed = Event.search([('modryn_is_booking', '=', True),
                         ('start', '>=', month_start),
                         ('start', '<', now),
                         ('modryn_outcome', '=', False)], order='start')
if unclosed and staff:
    # Roughly half sold, a third not, the rest no-shows: a conversion rate a
    # person can sanity-check rather than a column of one outcome.
    PATTERN = ['sold', 'sold', 'not_sold', 'sold', 'no_show', 'not_sold']
    PRICES = [4200, 7400, 8200, 9800, 11200, 13900]
    closed = 0
    for index, event in enumerate(unclosed):
        outcome = PATTERN[index % len(PATTERN)]
        who = staff[index % len(staff)]
        values = {
            'modryn_outcome': outcome,
            'modryn_outcome_by_id': who.id,
            'modryn_employee_id': event.modryn_employee_id.id or who.id,
        }
        if outcome == 'sold':
            values['modryn_sale_amount'] = PRICES[index % len(PRICES)]
        event.write(values)
        closed += 1
    say('bookings closed this month', closed)

# ------------------------------------------------------- the shifts: next week
# The screen opens on NEXT week and shows the published rota. qa's slots were
# all unpublished and had nobody on them, so the grid was a week of empty
# boxes on every tenant that had ever been seeded.
Slot = env['modryn.shift.slot'].sudo()
sunday = TODAY - _dt.timedelta(days=(TODAY.weekday() + 1) % 7)
weeks = [sunday, sunday + _dt.timedelta(days=7)]
filled = 0
for week in weeks:
    slots = Slot.search([('week_start', '=', week)], order='day')
    for index, slot in enumerate(slots):
        if not slot.employee_ids and staff:
            # Two on an ordinary shift, all three on the Thursday evening -
            # the one the templates already call busier.
            how_many = 3 if slot.end_hour >= 20 else 2
            chosen = [staff[(index + step) % len(staff)].id
                      for step in range(min(how_many, len(staff)))]
            slot.employee_ids = [(6, 0, chosen)]
            filled += 1
        slot.published = True
say('shift slots filled and published', filled)

# ---------------------------------- her own rail, and her own follow-ups
# The last two boxes on the main screen. Both read off the SIGNED-IN person,
# like the rest of it, and qa's twenty-three alterations belonged to nobody -
# so every one of the three saw an empty rail and an empty follow-up list.
Alteration = env['modryn.alteration.task'].sudo()
loose = Alteration.search([('seamstress_id', '=', False),
                           ('state', '!=', 'delivered')], limit=9)
for index, task in enumerate(loose):
    task.seamstress_id = staff[index % len(staff)].id if staff else False
say('alterations given a seamstress', len(loose))

# A follow-up each: the call after a fitting that did not end in a dress. Two
# per person, so the list is a list rather than a single line.
Task = env['modryn.task'].sudo()
if staff and Task.search_count([('task_type', '=', 'follow_up'),
                                ('state', '=', 'open')]) < 3:
    AFTER = [
        ('להתקשר אחרי המדידה', '050-7654321', 'רוני אלמוג'),
        ('לשלוח תמונות של השובל', '052-8765432', 'עדי נחום'),
        ('לבדוק אם החליטה', '054-9876543', 'מירב כהן'),
    ]
    made = 0
    for index, (what, phone, who) in enumerate(AFTER):
        Task.create({
            'name': what,
            'task_type': 'follow_up',
            'employee_id': staff[index % len(staff)].id,
            'state': 'open',
            'customer_name': who,
            'customer_phone': phone,
            'due_at': _dt.datetime.now() + _dt.timedelta(days=index + 1),
        })
        made += 1
    say('follow-ups written', made)

env.cr.commit()

print('\nwhat the playground holds now:')
for label, model in (
        ('dresses published', 'product.template'),
        ('kinds of dress', 'modryn.dress.type'),
        ('who may book', 'modryn.customer.kind'),
        ('booking-hour rows', 'modryn.queue.hour'),
        ('opening windows', 'modryn.opening.hours'),
        ('discount codes', 'modryn.discount.code'),
        ('fitting rooms', 'modryn.fitting.room'),
        ('closures', 'modryn.closure'),
        ('announcements', 'modryn.announcement'),
        ('sales', 'modryn.sale')):
    domain = [('is_published', '=', True)] if model == 'product.template' else []
    say(label, env[model].with_context(active_test=False).search_count(domain))
