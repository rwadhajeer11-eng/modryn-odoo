# A couple of alteration tasks so the workshop dashboard has something to show
# before anyone has finished a real fitting. Idempotent.
#
#   MODRYN_SLUG=bella ./odoo/odoo-bin shell -c odoo.conf -d bella \
#       --db-filter='^bella$' --no-http < scripts/seed_atelier.py

import os
from datetime import date, timedelta

SLUG = os.environ.get('MODRYN_SLUG', 'bella')

Task = env['modryn.alteration.task'].sudo()
Piece = env['modryn.garment.piece'].sudo()
Employee = env['hr.employee'].sudo()


def piece(name):
    return Piece.search([('name', '=', name)], limit=1)


# The seamstress in each demo boutique.
seamstress = Employee.search([('modryn_role_id.name', 'ilike', 'תופרת')], limit=1) \
    or Employee.search([('modryn_level', '=', 'staff')], limit=1)

variant = env['product.product'].sudo().search(
    [('product_tmpl_id.is_published', '=', True)], limit=1)

DEMO = [
    # (customer, phone, pieces, note, days_until_due, state)
    ("רותם כהן", "052-1110001", ['מכפלת'], "Raise the hem by 3 cm", 3, 'intake'),
    ("שירה לוי", "052-1110002", ['מחוך', 'כתפיות'], "Take in the bodice one size", 1, 'in_progress'),
    # Deliberately overdue, so the dashboard's overdue flag is visibly exercised.
    ("נועה בר", "052-1110003", ['שובל'], "Shorten the train", -2, 'in_progress'),
]

created = []
for name, phone, piece_names, note, offset, state in DEMO:
    if Task.search_count([('customer_name', '=', name)]):
        continue
    pieces = Piece.browse([p.id for p in (piece(n) for n in piece_names) if p])
    task = Task.create({
        'customer_name': name,
        'customer_phone': phone,
        'variant_id': variant.id if variant else False,
        'piece_ids': [(6, 0, pieces.ids)],
        'note': note,
        'seamstress_id': seamstress.id if seamstress else False,
        'due_date': date.today() + timedelta(days=offset),
        'state': state,
    })
    created.append((name, state, task.due_date.isoformat(), task.is_overdue))

env.cr.commit()
print('SEEDED_ATELIER %s' % SLUG)
for row in created:
    print('  %s | %s | due=%s | overdue=%s' % row)
print('TOTAL_TASKS=%s PIECES=%s' % (Task.search_count([]), Piece.search_count([])))
