#!/usr/bin/env bash
# The six-digit code currently live for a phone number, recovered by brute force.
#
# There is no other way, and that is by design. modryn.otp.code stores only
# hmac_sha256(database.secret, "<e164>|<code>") — addons/modryn_portal/models/otp.py:39-45 —
# so the code cannot be read back out of the row. Reading it from the log needs
# `log_handler = odoo.addons.modryn_portal.models.sms:INFO`, which is GLOBAL: it would write
# every real customer's code and every booking link into the journal, for every tenant.
#
# So recompute it. The code space is 10^6 and one million HMACs is under two seconds, which
# is why this is the established idiom here rather than a new trick — scripts/verify.sh:253
# reverses a booking token exactly this way, and qa/lib/otp.js does the same in Node.
#
#   ./scripts/otp_code.sh bella +972531112222
#
# Development and QA only. It needs local psql and the database secret; it proves nothing
# about production, where the same brute force is what an attacker would have to do against
# a five-minute TTL, a five-attempt cap and three sends per hour.
set -euo pipefail

DB="${1:?usage: otp_code.sh <db> <e164-phone>}"
PHONE="${2:?usage: otp_code.sh <db> <e164-phone>}"

SECRET=$(psql -d "$DB" -tAc "select value from ir_config_parameter where key='database.secret'")
[ -n "$SECRET" ] || { echo "no database.secret in $DB" >&2; exit 1; }

# Newest UNUSED code, matching modryn.otp.code.verify()'s own selection
# (otp.py:83-85) so this cannot disagree with the row the server will check.
HASH=$(psql -d "$DB" -tAc \
  "select code_hash from modryn_otp_code
    where phone = '$PHONE' and used_at is null
    order by create_date desc limit 1")
[ -n "$HASH" ] || { echo "no live code for $PHONE in $DB" >&2; exit 1; }

SECRET="$SECRET" PHONE="$PHONE" HASH="$HASH" python3 -c '
import hashlib, hmac, os, sys
secret, phone, want = os.environ["SECRET"].encode(), os.environ["PHONE"], os.environ["HASH"]
for n in range(10 ** 6):
    code = "%06d" % n
    if hmac.new(secret, ("%s|%s" % (phone, code)).encode(), hashlib.sha256).hexdigest() == want:
        print(code)
        sys.exit(0)
sys.exit("no six-digit code matches that hash — wrong database secret?")
'
