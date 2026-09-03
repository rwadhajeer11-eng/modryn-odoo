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
    WEEK = {
        '6': [(10, 2), (11, 2), (12, 2), (13, 1), (14, 1), (15, 2), (16, 2), (17, 2)],
        '0': [(10, 2), (11, 2), (12, 2), (13, 1), (14, 1), (15, 2), (16, 2), (17, 2)],
        '1': [(10, 2), (11, 2), (12, 2), (13, 1), (14, 1), (15, 2), (16, 2), (17, 2)],
        '2': [(10, 2), (11, 2), (12, 2), (13, 1), (14, 1), (15, 2), (16, 2), (17, 2)],
        '3': [(10, 2), (11, 2), (12, 2), (13, 1), (14, 1), (15, 2), (16, 2),
              (17, 3), (18, 3), (19, 3), (20, 2)],
        '4': [(9, 2), (10, 2), (11, 2), (12, 1)],
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
