# Loads Twilio credentials from the environment into this tenant's config.
# Runs inside `odoo-bin shell`. Credentials live in the gitignored .env and are
# NEVER committed:
#
#   set -a && . ./.env && set +a
#   ./odoo/odoo-bin shell -c odoo.conf -d bella --db-filter='^bella$' --no-http \
#       < scripts/configure_twilio.py
#
# With no credentials present the sender falls back to logging the message, so
# development and tests work unconfigured and never text a real person.

import os

icp = env['ir.config_parameter'].sudo()

MAPPING = {
    'modryn.twilio.account_sid': os.environ.get('TWILIO_ACCOUNT_SID'),
    'modryn.twilio.api_key_sid': os.environ.get('TWILIO_API_KEY_SID'),
    'modryn.twilio.api_key_secret': os.environ.get('TWILIO_API_KEY_SECRET'),
    'modryn.twilio.from_number': os.environ.get('TWILIO_FROM_NUMBER'),
}

missing = [k for k, v in MAPPING.items() if not v]
for key, value in MAPPING.items():
    if value:
        icp.set_param(key, value)

# The boutique's own cancellation policy, shown to a customer at the moment she
# cancels rather than buried in the booking form.
if not icp.get_param('modryn.cancellation_terms'):
    icp.set_param(
        'modryn.cancellation_terms',
        "Appointments may be cancelled free of charge up to 24 hours in advance. "
        "Later cancellations and no-shows may be subject to a fee.",
    )

# odoo-bin shell does NOT commit on exit — without this the parameters are
# written inside a transaction that is rolled back, and the success message
# above prints values that vanish the moment the shell closes.
env.cr.commit()

configured = not missing
print('TWILIO_CONFIGURED=%s' % configured)
if missing:
    print('MISSING=%s' % ', '.join(missing))
    print('SENDER=log (messages will be written to the server log, not sent)')
else:
    # Never print the secret itself — this output ends up in transcripts.
    print('SENDER=twilio from=%s' % icp.get_param('modryn.twilio.from_number'))
