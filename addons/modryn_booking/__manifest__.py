{
    'name': 'MODRYN Booking',
    'summary': 'Dual-path fitting appointments: book a specific dress, or book a consultation.',
    'description': """
Odoo Community has no online booking module (Appointment is Enterprise), so this
builds the PRD's dual booking paths directly on calendar.event:

  /book                    standalone consultation
  /book/dress/<id>         bound to a dress + size, entered from the product page

Deliberately NOT in scope for the PoC (each is a Phase-2 line item):
  - availability engine (opening hours -> slots, per-window capacity)
  - phone OTP verification
  - deposit capture through an Israeli PSP
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
    'depends': ['website_sale', 'calendar', 'modryn_theme'],
    'data': [
        'views/calendar_views.xml',
        'views/templates.xml',
    ],
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
