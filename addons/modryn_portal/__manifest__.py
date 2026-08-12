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

Everything she is not actively waiting on is queued in modryn.sms.outbox and drained by a
cron woken via _trigger(), so a degraded Twilio can no longer hold an HTTP worker for ten
seconds per booking. The login code stays synchronous — she is watching the screen for it.
""",
    'category': 'Website',
    # 1.4.0, because bella, noga AND modryn_template are all recorded at
    # 19.0.1.3.0 (`select latest_version from ir_module_module where
    # name='modryn_portal'`) and Odoo only runs a migration when the manifest
    # version rises PAST the version in ir_module_module. Re-shipping 1.3.0 would
    # be a no-op on every database that exists, and the slot index would keep its
    # old (start) definition on all three.
    #
    # 1.3.0's directory STAYS, unlike 1.2.0's, which was deleted. Odoo runs every
    # eligible version in order, so a database still below 1.3.0 runs both — and
    # both delegate to the same two schema_guard entry points, which are
    # idempotent by construction. Deleting it would buy nothing and lose the
    # record of when the dedupe first shipped.
    'version': '19.0.1.4.0',
    # migrations/19.0.1.4.0/ covers `-u`. These two cover `-i`, which is the path
    # every boutique cloned from modryn_template actually takes. See schema_guard.
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
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
