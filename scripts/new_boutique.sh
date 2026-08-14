#!/usr/bin/env bash
# Provision one boutique tenant: a full PostgreSQL database cloned from the
# template, plus its filestore, plus the per-tenant fixups that a raw
# `createdb -T` does NOT do for you.
#
# This script IS the tenancy-ops evidence for the scorecard: every line here is
# work that MODRYN's RLS model gets for free.
#
# Usage: ./scripts/new_boutique.sh bella "Bella Bridal"
#
# MODRYN_SMS_DISABLED=1 ./scripts/new_boutique.sh qa "QA Bridal" provisions a
# tenant that can never text a real person — this is how QA and loadtest
# databases are made. See the fixup that sets modryn.twilio.disabled below.
set -euo pipefail

SLUG="${1:?usage: new_boutique.sh <slug> \"<Display Name>\"}"
NAME="${2:?usage: new_boutique.sh <slug> \"<Display Name>\"}"
PORT="${PORT:-8069}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
# shellcheck disable=SC1091
source .venv/bin/activate

TEMPLATE=modryn_template
FILESTORE="$REPO/.odoo-data/filestore"

# The slug becomes a hostname label AND a database name. Keep it strict.
if ! [[ "$SLUG" =~ ^[a-z][a-z0-9-]{1,30}$ ]]; then
  echo "!! slug must be lowercase alnum/dash, starting with a letter"
  exit 1
fi

if psql -d postgres -tAc "select 1 from pg_database where datname='$SLUG'" | grep -q 1; then
  echo "!! database '$SLUG' already exists"
  exit 1
fi

# createdb -T requires ZERO open connections to the source database. If Odoo is
# running against the template this fails with a confusing error, so say it plainly.
CONNS=$(psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='$TEMPLATE'")
if [ "$CONNS" != "0" ]; then
  echo "!! $CONNS connection(s) open to $TEMPLATE — stop the Odoo server first"
  exit 1
fi

echo "==> cloning database $TEMPLATE -> $SLUG"
createdb -T "$TEMPLATE" "$SLUG"

# A clone inherits exactly what the template had, and a template built before the
# modryn addons were added to build_template.sh produces a tenant that 404s on
# /book and /floor — while the missing unique indexes announce nothing at all
# until two brides hold the same hour. Check the clone, not the template: this is
# the last point where the answer is still about THIS tenant.
for idx in calendar_event_modryn_one_live_booking_per_slot \
           modryn_day_waitlist_modryn_one_offer_per_day \
           modryn_queue_entry_modryn_open_phone_uniq; do
  if ! psql -d "$SLUG" -tAc "select to_regclass('$idx')" | grep -q .; then
    echo "!! $SLUG cloned from a $TEMPLATE that has no $idx."
    echo "   Rebuild the template: dropdb $TEMPLATE && ./scripts/build_template.sh"
    dropdb "$SLUG"
    exit 1
  fi
done

# Attachments live on disk in a directory named after the database. A cloned DB
# points at filestore rows that would otherwise resolve to nothing.
if [ -d "$FILESTORE/$TEMPLATE" ]; then
  echo "==> copying filestore"
  cp -R "$FILESTORE/$TEMPLATE" "$FILESTORE/$SLUG"
fi

echo "==> applying per-tenant fixups"
MODRYN_SLUG="$SLUG" MODRYN_NAME="$NAME" MODRYN_PORT="$PORT" \
MODRYN_SMS_DISABLED="${MODRYN_SMS_DISABLED:-}" \
./odoo/odoo-bin shell -c odoo.conf -d "$SLUG" --db-filter="^$SLUG\$" --no-http <<'PY'
import os, uuid
slug = os.environ['MODRYN_SLUG']
name = os.environ['MODRYN_NAME']
port = os.environ['MODRYN_PORT']

ICP = env['ir.config_parameter'].sudo()

# A duplicated database.uuid makes two tenants look like one instance to cron
# bookkeeping and to Odoo's own publisher warranty. Always regenerate.
ICP.set_param('database.uuid', str(uuid.uuid4()))

# The same argument, and it was missed here until 2026-08-11. database.secret is
# the HMAC key behind every signed thing Odoo and MODRYN produce: CSRF tokens,
# session tokens, the OTP hashes in otp.py, and the booking token in
# booking_comms.py. `createdb -T` copies it, so before this line every tenant
# cloned from the template shared one key — and _modryn_token() is HMAC over
# "booking:<id>", which made bella's token for booking 7 byte-identical to
# noga's. One boutique's reminder link opened, confirmed and CANCELLED another
# boutique's appointment, firing that boutique's day-waitlist. Always regenerate.
ICP.set_param('database.secret', str(uuid.uuid4()))

base = 'http://%s.localtest.me:%s' % (slug, port)
ICP.set_param('web.base.url', base)
# Otherwise the first login overwrites base.url with whatever host was used.
ICP.set_param('web.base.url.freeze', 'True')

env.company.name = name
site = env['website'].search([], limit=1)
site.write({'name': name, 'domain': base})

# A tenant used to be UNABLE to text anyone until someone ran
# configure_twilio.py against it, and qa/lib/guard.js leaned on exactly that —
# zero modryn.twilio.* parameters was its proof that a database could not dial
# out. That property is gone. The sender now falls back to the four
# TWILIO_* variables in the server's own environment, so a fresh clone can text
# a real bride the moment it exists, and an empty parameter table proves
# nothing. This flag is the only thing that still makes a tenant safe, which is
# why it is set here, at creation, and not left to a follow-up command someone
# runs after the loadtest seeder has already dialled out.
if os.environ.get('MODRYN_SMS_DISABLED'):
    ICP.set_param('modryn.twilio.disabled', '1')

env.cr.commit()
print('TENANT_READY %s -> %s' % (name, base))
PY

echo
echo "Provisioned: http://$SLUG.localtest.me:$PORT"
