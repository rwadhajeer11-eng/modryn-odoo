# Seeds one boutique's dress catalog. Runs inside `odoo-bin shell`.
# Tenant-specific content: run it per database, with different data each time,
# so the isolation check has something real to prove.
#
# Usage: MODRYN_SLUG=bella odoo-bin shell -d bella ... < scripts/seed_catalog.py

import base64
import io
import os

from PIL import Image, ImageDraw

SLUG = os.environ.get('MODRYN_SLUG', 'bella')

# MODRYN tokens (theme.css). Placeholder art stays inside the palette so the
# storefront screenshots judge the THEME, not stock photography.
CREAM = (253, 251, 247)
SURFACE = (246, 240, 230)
GOLD = (197, 160, 89)
INK = (43, 33, 24)

CATALOGS = {
    'bella': [
        # (name, price, sizes {size: qty}, price_visible)
        ('שמלת כלה אמילי', 8900.0, {'36': 2, '38': 1, '40': 0}, True),
        ('שמלת ערב ורוניקה', 4200.0, {'34': 1, '36': 3, '38': 2}, True),
        ('שמלת כלה קלרה', 12500.0, {'36': 1, '38': 0, '40': 1}, False),
    ],
    'noga': [
        ('שמלת כלה נועה', 6500.0, {'36': 1, '38': 2}, True),
        ('שמלת ערב תמר', 3800.0, {'38': 0, '40': 2, '42': 1}, True),
    ],
    # The public Railway demo. Five dresses so /shop, the size matrix, the
    # out-of-stock label and the price-on-request toggle all have something to
    # show; one hidden price and one zero-quantity size on purpose.
    'te': [
        ('שמלת כלה אורורה', 9800.0, {'36': 2, '38': 1, '40': 1}, True),
        ('שמלת כלה ליבי', 7400.0, {'34': 1, '36': 2, '38': 2}, True),
        ('שמלת ערב מיקה', 3900.0, {'36': 3, '38': 1, '42': 1}, True),
        ('שמלת כלה שירה', 11900.0, {'36': 1, '38': 1}, False),
        ('שמלת ערב רומי', 4600.0, {'38': 0, '40': 2}, True),
    ],
}


def swatch(seed, w=900, h=1200):
    """A calm placeholder in the brand palette: cream field, gold hairline frame."""
    img = Image.new('RGB', (w, h), CREAM if seed % 2 == 0 else SURFACE)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, w - 40, h - 40], outline=GOLD, width=3)
    # A simple centred lozenge — reads as a garment silhouette without pretending
    # to be a photograph.
    cx, cy = w // 2, h // 2
    d.polygon([(cx, cy - 320), (cx + 190, cy + 300), (cx - 190, cy + 300)],
              outline=GOLD, width=3)
    d.ellipse([cx - 34, cy - 380, cx + 34, cy - 312], outline=INK, width=3)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    return base64.b64encode(buf.getvalue())


# --- Size attribute --------------------------------------------------------
# One attribute, reused by every dress. create_variant='always' so each size is
# a real product.product that stock can count.
Attribute = env['product.attribute']
size_attr = Attribute.search([('name', '=', 'מידה')], limit=1)
if not size_attr:
    size_attr = Attribute.create({
        'name': 'מידה',
        'display_type': 'pills',
        'create_variant': 'always',
    })

Value = env['product.attribute.value']


def size_value(code):
    val = Value.search([('attribute_id', '=', size_attr.id), ('name', '=', code)], limit=1)
    return val or Value.create({'attribute_id': size_attr.id, 'name': code})


# --- Warehouse stock location ---------------------------------------------
stock_location = env['stock.warehouse'].search([], limit=1).lot_stock_id

created = []
for idx, (name, price, sizes, price_visible) in enumerate(CATALOGS[SLUG]):
    if env['product.template'].search_count([('name', '=', name)]):
        continue

    values = [size_value(code) for code in sizes]
    tmpl = env['product.template'].create({
        'name': name,
        'list_price': price,
        'type': 'consu',
        'is_storable': True,      # Odoo 19: 'goods' + track inventory
        'is_published': True,
        'modryn_price_visible': price_visible,
        'image_1920': swatch(idx),
        'attribute_line_ids': [(0, 0, {
            'attribute_id': size_attr.id,
            'value_ids': [(6, 0, [v.id for v in values])],
        })],
    })

    # Gallery: extra images so the product page has something to page through.
    for n in range(2):
        env['product.image'].create({
            'name': '%s %d' % (name, n + 2),
            'product_tmpl_id': tmpl.id,
            'image_1920': swatch(idx + n + 1),
        })

    # Per-size quantities. A zero here is what makes the storefront show a size
    # as unavailable — the "Size 38: 0 items" cell of the PRD's matrix.
    for variant in tmpl.product_variant_ids:
        code = variant.product_template_attribute_value_ids[:1].name
        qty = sizes.get(code, 0)
        if qty:
            env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': variant.id,
                'location_id': stock_location.id,
                'inventory_quantity': qty,
            }).action_apply_inventory()

    created.append((name, len(tmpl.product_variant_ids)))

env.cr.commit()
print('SEEDED %s: %s' % (SLUG, created))
print('TOTAL_PUBLISHED=%s' % env['product.template'].search_count([('is_published', '=', True)]))
