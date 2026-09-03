# -*- coding: utf-8 -*-
"""Give the platform's three tiers a price and a shape, so they can be read.

The register shipped with Basic, Standard and Premium and nothing behind any of
them: no price, no screens, no extras. A tier screen with three empty cards
cannot show whether the tier screen works.

Only the PLATFORM database, which holds no boutique's data at all.

    ./odoo/odoo-bin shell -c odoo.conf -d platform --no-http \
        < scripts/seed_platform_demo.py
"""
DB = env.cr.dbname
assert DB == 'platform', (
    "This is the platform's own register; %r is not it." % DB)


def say(what, count):
    print('  %-32s %s' % (what, count))


# ------------------------------------------------------------------ extras
# Things a subscription includes that are NOT screens: the list that box
# exists for.
EXTRAS = [
    ('SMS reminders', 'sms', 'A text the day before a fitting'),
    ('Two branches', 'branches', 'One shop on two addresses'),
    ('Phone support', 'support', 'A person, not a form'),
]
Feature = env['modryn.platform.feature']
made = 0
for name, code, note in EXTRAS:
    if not Feature.with_context(active_test=False).search_count(
            [('code', '=', code)]):
        Feature.create({'name': name, 'code': code, 'note': note})
        made += 1
say('extras (new)', made)

# ------------------------------------------------------------------- tiers
Screen = env['modryn.platform.screen']
Section = env['modryn.platform.section']


def screens(*keys):
    return Screen.search([('key', 'in', list(keys))])


def boxes(*keys):
    return Section.search([('key', 'in', list(keys))])


# A plain tier that opens the front of the shop and the manager's screen with
# only two panels on it — the exact case the platform owner described — then
# two that widen out from it.
PLANS = {
    'Basic': {
        'price': 249,
        'note': 'The shop, the diary and the team',
        'screens': screens('home', 'profile', 'floor', 'boss', 'checkin'),
        'boxes': boxes('announce', 'team'),
        'extras': ['sms'],
    },
    'Standard': {
        'price': 449,
        'note': 'Selling, the rail and the roster',
        'screens': screens('home', 'profile', 'floor', 'boss', 'checkin',
                           'sell', 'dresses', 'roster', 'shifts', 'roles',
                           'supervisor'),
        'boxes': boxes('announce', 'team', 'worked', 'rooms', 'hours'),
        'extras': ['sms', 'support'],
    },
    'Premium': {
        'price': 749,
        'note': 'Everything, the workshop and the figures',
        'screens': Screen.search([]),
        'boxes': Section.search([]),
        'extras': ['sms', 'support', 'branches'],
    },
}

Type = env['modryn.subscription.type'].with_context(active_test=False)
touched = 0
for name, plan in PLANS.items():
    tier = Type.search([('name', '=ilike', name)], limit=1)
    if not tier:
        continue
    # Only a tier nobody has priced: a price he typed himself is his answer,
    # not a gap to fill.
    if tier.price:
        continue
    tier.write({
        'price': plan['price'],
        'note': plan['note'],
        'screen_ids': [(6, 0, plan['screens'].ids)],
        'section_ids': [(6, 0, plan['boxes'].ids)],
        'feature_ids': [(6, 0, Feature.search(
            [('code', 'in', plan['extras'])]).ids)],
    })
    touched += 1
say('tiers priced and shaped', touched)

env.cr.commit()

print('\nthe register now reads:')
for tier in Type.search([]):
    print('  %-12s %6s ₪   %2d screens, %2d boxes, %d extras'
          % (tier.name, int(tier.price), len(tier.screen_ids),
             len(tier.section_ids), len(tier.feature_ids)))
