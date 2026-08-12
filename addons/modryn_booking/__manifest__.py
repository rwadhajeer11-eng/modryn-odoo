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

Each window also carries a CAPACITY — how many fittings the boutique can run at
once in that window. A booking takes a seat (0 .. capacity-1) and modryn_portal's
unique index keys on (start, seat), so it is Postgres, not a controller's
read-then-write, that decides who gets the last place. At capacity 1 that index
is exactly as strict as the (start) index it replaces.

Per-appointment-type DURATION is deliberately still out, and not out of laziness:
a 90-minute fitting overlaps the following hour, and no unique index on a start
time can express that. It needs a tstzrange EXCLUDE constraint over btree_gist and
a grid that is no longer uniform. Shipping half of it means offering 11:00 while a
90-minute fitting runs through it, which is worse than not offering it at all.

Deliberately NOT in scope for the PoC (each is a Phase-2 line item):
  - per-appointment-type duration (see above)
  - phone OTP verification
  - deposit capture through an Israeli PSP
""",
    'category': 'Website',
    # DO NOT bump this for a new model. verify.sh §24 asserts the version has a
    # MATCHING migrations/<version>/ directory, because a bumped manifest with
    # no directory means the upgrade path never runs and bella and noga silently
    # keep the old data while freshly cloned boutiques get the new. A new model
    # needs no migration at all — the ORM creates modryn_closure's table on `-u`
    # whatever this string says — so bumping would buy an empty directory and
    # one more thing that can drift. Bump when DATA has to move, not when a
    # table appears.
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
