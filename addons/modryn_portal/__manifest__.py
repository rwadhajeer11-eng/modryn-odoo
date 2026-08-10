{
    'name': 'MODRYN Customer Portal',
    'summary': 'Phone + SMS OTP login so a customer can see and cancel her own appointments.',
    'description': """
Stage 2 of the Odoo evaluation. A bride books as a guest today and then has no way back in;
this gives her one, without asking her to invent a password for a dress shop.

  /my/login     enter your phone number
  /my/verify    enter the 6-digit code we texted you
  /my/bookings  your upcoming and past appointments, and cancel an upcoming one

Deliberately NO res.users account is created for customers. A verified customer is a
partner id held in the session — which sidesteps portal-account provisioning entirely and
keeps customers off the Enterprise seat count forever.

SMS goes through a small sender port: a real Twilio adapter when credentials are
configured, otherwise a log-only sender so development and tests never text anybody.
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
    'depends': ['website', 'modryn_booking', 'modryn_theme'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/website_menu_data.xml',
        'views/portal_templates.xml',
        'views/booking_link_templates.xml',
        'views/waitlist_templates.xml',
    ],
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
