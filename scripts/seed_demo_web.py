# Web-presence fixups the module layer cannot ship. Runs inside `odoo-bin shell`.
# Idempotent — safe to re-run on any tenant.
#
#   MODRYN_SLUG=te ./odoo/odoo-bin shell -c odoo.conf -d te \
#       --db-filter='^te$' --no-http < scripts/seed_demo_web.py
#
# Three jobs:
#   1. Company identity — the footer renders phone/email/address from
#      res.company, and new_boutique.sh sets only the name. Never overwrites
#      a value somebody already set.
#   2. Menu translations — website.menu.create() copies a website-less menu
#      onto every website, and those COPIES get no .po translations (the
#      generic record does; the copy's he_IL is null). Fixed per tenant here.
#      Also breaks the My-appointments/Contact-us 60/60 sequence tie.
#   3. modryn.sms_demo — te only. With no Twilio configured, OTP codes go to
#      the server log and every phone-code flow dead-ends for the public;
#      demo mode shows the code on the verify page instead. Never set this
#      on a tenant that texts real phones.

import os

SLUG = os.environ.get('MODRYN_SLUG', 'bella')

COMPANY_DEFAULTS = {
    'te': {
        'phone': '03-5550100',
        'email': 'hello@modryn.co.il',
        'street': 'דיזנגוף 99',
        'city': 'תל אביב',
    },
}

company = env.company
for field, value in COMPANY_DEFAULTS.get(SLUG, {}).items():
    if not company[field]:
        company[field] = value

# --- menu copies -----------------------------------------------------------
MENU_TRANSLATIONS = {
    '/book': {'en_US': 'Book an appointment', 'he_IL': 'קביעת תור', 'ar_001': 'حجز موعد'},
    '/my/bookings': {'en_US': 'My appointments', 'he_IL': 'התורים שלי', 'ar_001': 'مواعيدي'},
}
Menu = env['website.menu'].sudo()
for url, translations in MENU_TRANSLATIONS.items():
    for menu in Menu.search([('url', '=', url)]):
        menu.update_field_translations('name', translations)
        if url == '/my/bookings' and menu.sequence == 60:
            # Shipped at 60, tying with Odoo's Contact us (also 60) — the nav
            # order was left to the id tiebreak.
            menu.sequence = 70

# --- demo OTP display ------------------------------------------------------
if SLUG == 'te':
    env['ir.config_parameter'].sudo().set_param('modryn.sms_demo', '1')

env.cr.commit()
print('SEEDED_DEMO_WEB %s' % SLUG)
print('COMPANY phone=%s email=%s street=%s' % (company.phone, company.email, company.street))
print('SMS_DEMO=%s' % (env['ir.config_parameter'].sudo().get_param('modryn.sms_demo') or ''))
print('BOOK_MENUS=%s' % Menu.search_count([('url', '=', '/book')]))
