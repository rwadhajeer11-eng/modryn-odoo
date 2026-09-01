# Set the platform owner's phone and identity number.
#
#   MODRYN_PLATFORM_LOGIN=admin \
#   MODRYN_PLATFORM_PHONE=052-1234567 \
#   MODRYN_PLATFORM_ID=123456789 \
#     ./odoo/odoo-bin shell -c odoo.conf -d platform --db-filter='^platform$' \
#       --no-http < scripts/platform_factors.py
#
# WHY A SCRIPT AND NOT A SCREEN. These two answers are asked FOR at the door;
# a page inside the platform that sets them would be a page reachable only by
# somebody who already knows them, which is the wrong way round the first time
# and no use at all if they are ever forgotten.
#
# THIS IS ALSO THE WAY BACK IN. /web/login no longer admits the platform owner —
# four questions are only worth four if nothing accepts two — so if these two
# answers are lost, this script run on the machine is the recovery. odoo-bin
# shell goes through no login at all.
#
# NOTHING IS PRINTED BACK. The values are hashed through the same passlib
# context Odoo hashes passwords with, and no code in this product can read
# either of them again — not this script, not the login, not a database dump.
# What it prints is whether each one is now set, and nothing more.

import os

LOGIN = os.environ.get('MODRYN_PLATFORM_LOGIN', 'admin')
PHONE = os.environ.get('MODRYN_PLATFORM_PHONE')
IDNUM = os.environ.get('MODRYN_PLATFORM_ID')

if not PHONE and not IDNUM:
    raise SystemExit(
        "Nothing to set. Give MODRYN_PLATFORM_PHONE, MODRYN_PLATFORM_ID, or "
        "both — the script changes only what it is handed, so one can be reset "
        "without touching the other.")

user = env['res.users'].sudo().search([('login', '=', LOGIN)], limit=1)
if not user:
    raise SystemExit("No account called %r on this database." % LOGIN)

group = env.ref('modryn_platform.group_platform_owner', raise_if_not_found=False)
if not group or group not in user.group_ids:
    # Refused rather than done anyway: setting these on an account that cannot
    # open the register writes two secrets nothing will ever check, and the
    # person who typed the wrong login would have no way to tell.
    raise SystemExit(
        "%r is not the platform owner, so these answers would never be asked "
        "for. Nothing was changed." % LOGIN)

user.modryn_set_platform_factors(phone=PHONE, idnum=IDNUM)
env.cr.commit()

stored = user.sudo().read(['modryn_platform_phone', 'modryn_platform_idnum'])[0]
print("")
print("  account:      %s" % LOGIN)
print("  phone:        %s" % ("set" if stored['modryn_platform_phone'] else "NOT SET"))
print("  ID number:    %s" % ("set" if stored['modryn_platform_idnum'] else "NOT SET"))
print("")
print("  Both must be set, or the sign-in refuses this account: the extra")
print("  questions are not optional for whoever holds it.")
print("")
