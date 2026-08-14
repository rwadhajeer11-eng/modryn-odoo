#!/usr/bin/env bash
# Restore load tenants from the gold snapshot, between ramp stages.
#
#   ./loadtest/seed/reset_tenants.sh lt01 lt02
#   ./loadtest/seed/reset_tenants.sh --all          # every tenant in tenants.json
#
# THE ODOO SERVER MUST BE STOPPED. dropdb blocks on Odoo's pooled connections —
# they outlive the request that opened them — and `createdb -T` demands zero
# connections to the gold.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

GOLD="${MODRYN_GOLD:-modryn_gold_seeded}"
FILESTORE="$REPO/.odoo-data/filestore"
TENANTS_JSON="$REPO/loadtest/config/tenants.json"
PORT="${PORT:-8069}"

if [ "${1:-}" = "--all" ]; then
  [ -f "$TENANTS_JSON" ] || { echo "!! no $TENANTS_JSON — run gen_tenants.sh first"; exit 1; }
  # read -a, not mapfile: macOS still ships bash 3.2 as /bin/bash and this script
  # must not depend on which bash `env` happens to find.
  read -r -a SLUGS <<<"$(python3 -c "
import json,sys
print(' '.join(t['slug'] for t in json.load(open(sys.argv[1]))['tenants']))" "$TENANTS_JSON")"
else
  SLUGS=("$@")
fi
[ "${#SLUGS[@]}" -gt 0 ] || { echo "usage: reset_tenants.sh <slug>... | --all"; exit 1; }

# NOTHING used to stand between "$@" and `dropdb --force` below. `reset_tenants.sh
# bella` destroyed the live boutique's database AND its filestore, then recreated
# it from a load-test gold: every real appointment, customer and photo replaced by
# synthetic fixtures, mid-service, with no undo. --force terminates live backends,
# so "stop the server first" was never protection.
#
# Element-wise comparison against odoo.conf's db_name, NOT grep: a regex form
# follows "= " rather than "=" or "," and misses the first entry under GNU grep,
# while a substring form matches 'ella' inside 'bella' under BSD grep. Getting it
# wrong means the refusal silently stops refusing. Same function as
# deploy/scripts/restore.sh — the one lane that got this right first.
ODOO_CONF="${ODOO_CONF:-$REPO/odoo.conf}"
db_name_contains() {
  local line dbs d
  line="$(grep -E '^[[:space:]]*db_name[[:space:]]*=' "$ODOO_CONF" | head -n 1)"
  dbs="${line#*=}"; dbs="${dbs//[[:space:]]/}"
  local IFS=','
  for d in $dbs; do
    [ "$d" = "$1" ] && return 0
  done
  return 1
}

for SLUG in "${SLUGS[@]}"; do
  if db_name_contains "$SLUG"; then
    echo "!! REFUSING: '$SLUG' is a LIVE tenant (listed in db_name in $ODOO_CONF)."
    echo "   This script drops the database and its filestore. Load tenants are the"
    echo "   ones gen_tenants.sh created; reset those by name or with --all."
    exit 1
  fi
done
if db_name_contains "$GOLD"; then
  echo "!! REFUSING: gold snapshot '$GOLD' is a LIVE tenant — MODRYN_GOLD is misset."
  exit 1
fi

if ! psql -d postgres -tAc "select 1 from pg_database where datname='$GOLD'" | grep -q 1; then
  echo "!! no gold snapshot ($GOLD). Run make_gold.sh first."
  exit 1
fi

CONNS=$(psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='$GOLD'")
if [ "$CONNS" != "0" ]; then
  echo "!! $CONNS connection(s) open to $GOLD — stop the Odoo server first"
  exit 1
fi

# BEFORE the loop, because a check that runs after `dropdb` has already lost. The
# same test used to sit at the BOTTOM of the loop body — 54 lines past the
# dropdb/createdb/rm -rf it is supposed to prevent — so on a contaminated gold it
# could only stop the run after that tenant had been dropped, recreated from the
# contamination, and left on disk with the capture flag set. It never refused
# anything; it only announced the damage.
#
# Two sources, both checked here:
#  - the GOLD: every restored tenant is a byte copy of it, so one unmuted gold
#    hands thirty tenants a working sender in one command;
#  - each EXISTING target: a database that does not say it is muted is by
#    definition not a load tenant, and this script would drop it and its
#    filestore. That is the db_name guard's failure mode caught a second way,
#    before the damage.
#
# The test used to be "holds zero modryn.twilio.* parameters", and that was the
# same sentence read the other way round: no credentials in the database meant no
# credentials at all. Credentials now live in the server's environment and every
# database inherits them, so an empty ir_config_parameter proves nothing and the
# tenant has to carry an explicit modryn.twilio.disabled instead. Absent, empty
# or unreadable refuses — a database that never heard of the key fails closed,
# which is the only safe reading when the default is now "can send".
P_DISABLED=modryn.twilio.disabled
twilio_disabled() {
  psql -d "$1" -tAc "select value from ir_config_parameter where key='$P_DISABLED'" 2>/dev/null || echo ''
}
if [ -z "$(twilio_disabled "$GOLD")" ]; then
  echo "!! REFUSING: gold snapshot '$GOLD' carries no $P_DISABLED."
  echo "   Every tenant restored from it would send through the environment's"
  echo "   Twilio credentials. Re-cut the gold from a muted load tenant."
  exit 1
fi
for SLUG in "${SLUGS[@]}"; do
  psql -d postgres -tAc "select 1 from pg_database where datname='$SLUG'" | grep -q 1 || continue
  if [ -z "$(twilio_disabled "$SLUG")" ]; then
    echo "!! REFUSING: '$SLUG' carries no $P_DISABLED, so it can still send — it is"
    echo "   not a load tenant. This script drops it and its filestore. Nothing"
    echo "   dropped."
    exit 1
  fi
done

# The gold carries the identity of the tenant it was cut from, so every restored
# tenant would answer as that one. Read it back rather than assuming lt01.
GOLD_SECRET=$(psql -d "$GOLD" -tAc "select value from ir_config_parameter where key='modryn.loadtest.secret'")
GOLD_SOURCE=$(psql -d "$GOLD" -tAc "select value from ir_config_parameter where key='modryn.loadtest.gold_source'")
if [ -z "$GOLD_SOURCE" ]; then
  echo "!! $GOLD carries no modryn.loadtest.gold_source — it predates make_gold.sh."
  echo "   Re-cut it, or every restored tenant answers for the wrong phone prefix."
  exit 1
fi
GOLD_PREFIX="+97252${GOLD_SOURCE: -2}"

for SLUG in "${SLUGS[@]}"; do
  echo "==> $SLUG"
  # --force terminates leftover backends (PG 13+). Without it this blocks
  # indefinitely on a connection Odoo's pool is holding open past its request.
  dropdb --force --if-exists "$SLUG"
  createdb -T "$GOLD" "$SLUG"

  rm -rf "${FILESTORE:?}/$SLUG"
  [ -d "$FILESTORE/$GOLD" ] && cp -R "$FILESTORE/$GOLD" "$FILESTORE/$SLUG"

  # The per-tenant fixups. scripts/new_boutique.sh OWNS these — this is a copy,
  # and the copy exists because that script bundles them with a clone from
  # modryn_template, while here the database already exists and came from gold.
  #
  # Deliberately psql and not `odoo-bin shell`: a registry load is ~13 s, which
  # across 30 tenants is most of the reset budget, and every value below is a
  # plain scalar column. res_company.name is stored AND related to its partner,
  # so both rows are written or the header and the contact record disagree.
  psql -d "$SLUG" -q <<SQL
-- A duplicated database.uuid makes two tenants look like one instance to cron
-- bookkeeping and to Odoo's publisher warranty.
update ir_config_parameter set value = gen_random_uuid()::text where key = 'database.uuid';
update ir_config_parameter set value = 'http://$SLUG.localtest.me:$PORT' where key = 'web.base.url';
-- Without the freeze the first login overwrites base.url with whatever host it used.
insert into ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
values ('web.base.url.freeze', 'True', 1, 1, now(), now())
on conflict (key) do update set value = 'True';
update res_partner set name = 'Load Tenant ${SLUG: -2}'
  where id in (select partner_id from res_company);
update res_company set name = 'Load Tenant ${SLUG: -2}';
update website set name = 'Load Tenant ${SLUG: -2}',
                   domain = 'http://$SLUG.localtest.me:$PORT';
-- Renumber the seeded phones from the gold source's index to this tenant's. The
-- +97252TTVVVV scheme is a contract tenants.json publishes to the harness, and
-- without this every restored tenant except the gold's own would hold bookings
-- for numbers no VU ever logs in as — /my/bookings silently empty, on a page
-- whose whole job is to render a list.
update res_partner set phone = '+97252${SLUG: -2}' || substr(phone, 9)
  where phone like '$GOLD_PREFIX%' and length(phone) = 12;
update calendar_event set modryn_customer_phone = '+97252${SLUG: -2}' || substr(modryn_customer_phone, 9)
  where modryn_customer_phone like '$GOLD_PREFIX%' and length(modryn_customer_phone) = 12;
update modryn_queue_entry set phone = '+97252${SLUG: -2}' || substr(phone, 9)
  where phone like '$GOLD_PREFIX%' and length(phone) = 12;
-- The restore overwrote ir_config_parameter with gold's, so the loadtest keys
-- have to be re-asserted. They are gold's own values, but saying so explicitly
-- is what makes a hand-edited gold fail loudly instead of quietly.
insert into ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
values ('modryn.loadtest.enabled', '1', 1, 1, now(), now()),
       ('modryn.loadtest.secret', '$GOLD_SECRET', 1, 1, now(), now())
on conflict (key) do update set value = excluded.value, write_date = now();
-- Came along with the clone. Leaving it would have a restored tenant claim to be
-- the gold's source, which is a lie the next make_gold.sh reader would believe.
delete from ir_config_parameter where key = 'modryn.loadtest.gold_source';
SQL

  # A tripwire, NOT the guard — the guard is the pre-loop check above, which can
  # still refuse. This one can only report, because the tenant is already
  # restored by the time it runs. It is kept because it is one round trip on a
  # ~2 s operation and it covers the one case the pre-check cannot see: a future
  # edit to the SQL block above that drops the flag the gold carried.
  #
  # Which is also why that block does NOT re-assert modryn.twilio.disabled the way
  # it re-asserts the loadtest keys. The flag arrives with the byte copy and the
  # pre-loop check already proved the gold has one; leaving it alone is what lets
  # this line test the restore rather than test its own INSERT from one statement
  # earlier.
  if [ -z "$(twilio_disabled "$SLUG")" ]; then
    echo "!! $SLUG carries no $P_DISABLED AFTER restore, though the gold did —"
    echo "   this script cleared it. STOPPING; $SLUG is restored and would send"
    echo "   through the environment's Twilio credentials. Set the parameter"
    echo "   before starting the server."
    exit 1
  fi
done

echo
echo "Reset ${#SLUGS[@]} tenant(s) from $GOLD."
echo "Start the server, then wait for a 200 on the first tenant before the next stage:"
echo "  ./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1"
