{
    'name': 'MODRYN Booking',
    'summary': 'Dual-path fitting appointments: book a specific dress, or book a consultation.',
    'description': """
Odoo Community has no online booking module (Appointment is Enterprise), so this
builds the PRD's dual booking paths directly on calendar.event:

  /book                    standalone consultation
  /book/dress/<id>         bound to a dress + size, entered from the product page

The lattice those slots come from is the boutique's own data: modryn.opening.hours
holds one row per open window, several rows per weekday where the shop shuts for
the afternoon, and no row at all on a day it is closed. It lives here rather than
in modryn_staff so that the portal can read it too.

Deliberately NOT in scope for the PoC (each is a Phase-2 line item):
  - per-window capacity and per-appointment-type duration
  - phone OTP verification
  - deposit capture through an Israeli PSP
""",
    'category': 'Website',
    'version': '19.0.1.1.0',
    'depends': ['website_sale', 'calendar', 'modryn_theme'],
    # migrations/ covers `-u` on every existing database, modryn_template
    # included. This covers a genuine `-i`, which is how build_template.sh
    # rebuilds the golden template. new_boutique.sh runs neither — it clones
    # with `createdb -T` and inherits the template's rows. See schema_guard.
    'post_init_hook': 'post_init_hook',
    'data': [
        'security/ir.model.access.csv',
        'views/calendar_views.xml',
        'views/templates.xml',
    ],
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
