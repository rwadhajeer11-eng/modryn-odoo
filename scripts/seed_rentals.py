# -*- coding: utf-8 -*-
"""Gowns out on loan, for the playground.

qa only. Four rentals that between them show every state the screen has to
draw: one back on the rail, one out and not due, one out with the wedding just
gone, and one a fortnight past the wedding — which is the one the red banner
exists for and the only way to see that it works without waiting ten days.

Idempotent, like every other seeder here: it writes nothing it can already
find, so running it twice does not double the shop's rentals.

    ./odoo/odoo-bin shell -c odoo.conf -d qa --no-http < scripts/seed_rentals.py
"""
import datetime

assert env.cr.dbname == 'qa', 'the playground only — this writes customer rows'

TODAY = datetime.date.today()
Rental = env['modryn.rental']
Variant = env['product.product']

seller = env['hr.employee'].search(
    [('modryn_level', 'in', ('staff', 'manager'))], limit=1)

# A real gown off the rail where there is one, so the card links back to the
# catalogue the way a rental taken at the counter does.
domain = [('product_tmpl_id.is_published', '=', True)]
if 'modryn_is_accessory' in env['product.template']._fields:
    # Not the veils and the hair ribbons: a rental is about a gown, and a
    # screen showing one lent out for 1,100 shekels reads as a bug.
    domain.append(('product_tmpl_id.modryn_is_accessory', '=', False))
gowns = Variant.search(domain, limit=4)


def say(what, value):
    print('  %-34s %s' % (what, value))


# name, phone, days since the wedding (negative = still to come), rented for,
# whether it is back.
PEOPLE = [
    ('רותם אבני', '+972501110001', -21, 1200, False),
    ('נועה שגב', '+972501110002', -3, 900, False),
    ('הילה כהן', '+972501110003', 4, 1500, False),
    # The late one. Sixteen days past the wedding and never returned — six days
    # past the ten the shop allows, so the banner has something to shout about.
    ('מאיה לוי', '+972501110004', 16, 1100, False),
    ('שירה ברק', '+972501110005', 30, 800, True),
]

made = 0
for index, (name, phone, since, price, back) in enumerate(PEOPLE):
    if Rental.search_count([('customer_name', '=', name)]):
        continue
    gown = gowns[index % len(gowns)] if gowns else Variant.browse()
    size = gown.product_template_attribute_value_ids[:1].name if gown else ''
    kind = ''
    if gown and 'modryn_type_id' in gown.product_tmpl_id._fields:
        kind = gown.product_tmpl_id.modryn_type_id.name or ''
    wedding = TODAY - datetime.timedelta(days=since)
    Rental.create({
        'customer_name': name,
        'customer_phone': phone,
        'variant_id': gown.id if gown else False,
        'dress_label': '%s%s' % (
            gown.product_tmpl_id.name if gown else 'שמלה',
            ' · %s' % size if size else ''),
        'dress_kind': kind,
        'retail_price': round(gown.list_price or 0.0) if gown else 6000,
        'rental_price': price,
        'employee_id': seller.id if seller else False,
        # Collected a fortnight before the wedding, which is when a boutique
        # actually hands one over.
        'taken_at': datetime.datetime.combine(
            wedding - datetime.timedelta(days=14), datetime.time(12, 0)),
        'wedding_date': wedding,
        'returned_at': (datetime.datetime.combine(
            wedding + datetime.timedelta(days=3), datetime.time(12, 0))
            if back else False),
    })
    made += 1

say('rentals (new)', made)
say('rentals in total', Rental.search_count([]))
say('late right now', Rental.search_count(Rental.modryn_late_domain()))
env.cr.commit()
