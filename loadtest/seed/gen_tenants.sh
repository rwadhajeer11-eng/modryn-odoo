#!/usr/bin/env bash
# Create N load-test tenants and write loadtest/config/tenants.json.
#
#   MODRYN_DEMO_PASSWORD='...' ./loadtest/seed/gen_tenants.sh 2
#   MODRYN_DEMO_PASSWORD='...' ./loadtest/seed/gen_tenants.sh 30 --prefix lt --from 3
#
# THE ODOO SERVER MUST BE STOPPED. Every phase here either clones a database
# (`createdb -T` demands zero connections to the SOURCE) or loads a registry in
# `odoo-bin shell`. The script refuses to start otherwise rather than failing
# halfway through tenant nine.
set -euo pipefail

COUNT="${1:?usage: gen_tenants.sh <count> [--prefix lt] [--from 1]}"
shift
PREFIX=lt
FROM=1
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --from)   FROM="$2";   shift 2 ;;
    *) echo "!! unknown option: $1"; exit 1 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
# shellcheck disable=SC1091
source .venv/bin/activate

TEMPLATE=modryn_template
PORT="${PORT:-8069}"
CONFIG_DIR="$REPO/loadtest/config"
TENANTS_JSON="$CONFIG_DIR/tenants.json"
LOADTEST_ADDONS="$REPO/loadtest/odoo_addons"
ADDONS_PATH="$REPO/odoo/addons,$REPO/addons,$LOADTEST_ADDONS"

MANAGERS="${MODRYN_MANAGERS:-3}"
STAFF="${MODRYN_STAFF:-12}"

if [ -z "${MODRYN_DEMO_PASSWORD:-}" ]; then
  echo "!! MODRYN_DEMO_PASSWORD is unset. Pick one; the k6 harness reads the same"
  echo "   value from its own environment. It is deliberately NOT written to"
  echo "   tenants.json — a password in a config file is a password in a backup."
  exit 1
fi

if [ "$FROM" -lt 1 ] || [ $((FROM + COUNT - 1)) -gt 99 ]; then
  echo "!! tenant indices must land in 01..99 — that is the TT field of the"
  echo "   +97252TTVVVV phone scheme, and a third digit would collide tenants."
  exit 1
fi

# The check new_boutique.sh already makes, hoisted to the top so the operator
# learns at second one instead of after the first clone succeeds.
CONNS=$(psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='$TEMPLATE'")
if [ "$CONNS" != "0" ]; then
  echo "!! $CONNS connection(s) open to $TEMPLATE — stop the Odoo server first"
  exit 1
fi

# BEFORE the first clone, because the template is what every tenant is copied
# from: one contaminated template hands live SMS credentials to all thirty, and
# the per-tenant check at the bottom of the loop below could only notice that
# after tenant one was already created, seeded and left on disk with the capture
# flag set. A check downstream of the thing it guards is not a guard.
twilio_params() {
  psql -d "$1" -tAc "select count(*) from ir_config_parameter where key like 'modryn.twilio.%'" 2>/dev/null || echo 0
}
TW=$(twilio_params "$TEMPLATE")
if [ "$TW" != "0" ]; then
  echo "!! REFUSING: clone source '$TEMPLATE' has $TW modryn.twilio.* parameter(s)."
  echo "   Every tenant cloned from it would inherit live SMS credentials."
  echo "   Clear them from the template first. Nothing created."
  exit 1
fi

# One secret for the whole fleet: the harness carries a single LOADTEST_SECRET
# and per-tenant secrets would buy nothing that the enabled flag does not.
SECRET="${MODRYN_LOADTEST_SECRET:-$(openssl rand -hex 24)}"

mkdir -p "$CONFIG_DIR"
SLUGS=()

for n in $(seq "$FROM" $((FROM + COUNT - 1))); do
  IDX=$(printf '%02d' "$n")
  SLUG="${PREFIX}${IDX}"
  SLUGS+=("$SLUG")
  echo
  echo "################ $SLUG ($((n - FROM + 1))/$COUNT) ################"

  if psql -d postgres -tAc "select 1 from pg_database where datname='$SLUG'" | grep -q 1; then
    # An existing database is about to have the capture addon installed and load
    # fixtures written into it. Refuse before either write if it holds SMS
    # credentials: that is a real boutique reached by a slug collision, and
    # installing modryn_loadtest into it is the accident this repo keeps three
    # gates to prevent.
    TW=$(twilio_params "$SLUG")
    if [ "$TW" != "0" ]; then
      echo "!! REFUSING: '$SLUG' already exists and has $TW modryn.twilio.* parameter(s)."
      echo "   That is a live boutique, not a load tenant. Nothing written to it."
      exit 1
    fi
    echo "==> $SLUG exists; skipping clone+fixups"
  else
    # REUSED, not re-implemented: new_boutique.sh owns the clone, the filestore
    # copy, the uniqueness-index guard and the four per-tenant fixups (fresh
    # database.uuid, web.base.url, the freeze, company + website name). Those
    # fixups are the tenancy-ops evidence and duplicating them here would let the
    # copy rot.
    PORT="$PORT" ./scripts/new_boutique.sh "$SLUG" "Load Tenant $IDX"
  fi

  # modryn_loadtest is installed PER TENANT rather than into modryn_template.
  # Putting a staging-only capture addon into the template every real boutique is
  # cloned from is precisely the accident that keeping it outside addons/ exists
  # to prevent. --addons-path is passed explicitly so this works whether or not
  # odoo.conf has been edited yet.
  if [ "$(psql -d "$SLUG" -tAc "select count(*) from ir_module_module where name='modryn_loadtest' and state='installed'")" != "1" ]; then
    echo "==> installing modryn_loadtest into $SLUG"
    ./odoo/odoo-bin server -c odoo.conf -d "$SLUG" \
      --addons-path="$ADDONS_PATH" --db-filter="^$SLUG\$" \
      -i modryn_loadtest --stop-after-init --no-http >/dev/null
  fi

  # Seeding is ADDITIVE, so re-running this script on an existing tenant used to
  # book the grid a second time: 28 live bookings became 42, days that were meant
  # to be free filled up, and the fixture silently stopped matching what
  # tenants.json and the k6 workload model assume. The "exists; skipping
  # clone+fixups" message above made it read as a safe no-op. It never was.
  #
  # The marker is the seeded data itself rather than a flag file: a flag can
  # outlive a dropped database, and this question is only ever about what is in
  # the tenant right now.
  SEEDED=$(psql -d "$SLUG" -tAc "select count(*) from calendar_event
    where modryn_is_booking is true and modryn_cancelled_at is null and active is true" 2>/dev/null || echo 0)
  # Guarding only the seed step, NOT the rest of the loop body: the grid-alignment
  # assertion below must still run for an already-seeded tenant, because that is
  # the check which proves the fixture is still one /book would offer.
  if [ "${SEEDED:-0}" != "0" ]; then
    echo "==> $SLUG already carries $SEEDED live booking(s); skipping seed"
    echo "    (to re-seed from scratch: ./loadtest/seed/reset_tenants.sh $SLUG,"
    echo "     or drop the tenant and re-run this script)"
  else
    echo "==> seeding people, catalog, bookings and queue"
    MODRYN_SLUG="$SLUG" MODRYN_TENANT_INDEX="$IDX" \
    MODRYN_DEMO_PASSWORD="$MODRYN_DEMO_PASSWORD" \
    MODRYN_MANAGERS="$MANAGERS" MODRYN_STAFF="$STAFF" \
    ./odoo/odoo-bin shell -c odoo.conf -d "$SLUG" \
      --addons-path="$ADDONS_PATH" --db-filter="^$SLUG\$" --no-http \
      < loadtest/seed/seed_tenant.py
  fi

  # The pre-flight check above is the one that can refuse; this one runs after
  # seeding because seeding is the last step that could set a parameter, and it
  # can only stop the REMAINING tenants. Zero modryn.twilio.* is the property
  # modryn.sms._send_now branches on: with no config it logs the body and returns
  # ('logged'), so the whole flow runs at no cost and no delivery.
  #
  # Note what this is NOT protecting against: the seeded numbers are
  # +97252TTVVVV — 11 digits, one short of a deliverable Israeli mobile (see
  # seed_tenant.py's assert_phone_scheme). A tenant that inherited credentials
  # would get Twilio 21211 rejects, not delivered texts. That makes this a
  # correctness gate, not the last line before the phone bill — and it stays
  # exactly as strict, because the day someone makes the numbers deliverable is
  # the day it becomes the latter.
  TW=$(twilio_params "$SLUG")
  if [ "$TW" != "0" ]; then
    echo "!! $SLUG has $TW modryn.twilio.* parameter(s) after seeding. REFUSING."
    echo "   Seeding wrote them; clear them before running against this tenant."
    exit 1
  fi

  # The seeded bookings must sit on the grid /book itself offers, because
  # /book/submit now refuses any other time and verify.sh asserts the same thing
  # against the rows. seed_tenant.py imports the grid constants so it cannot
  # drift — this re-asserts the RESULT, in the same SQL verify.sh uses, because
  # an import proves the constants match and not that the arithmetic did.
  BOGUS=$(psql -d "$SLUG" -tAc "select count(*) from calendar_event
    where modryn_is_booking is true and modryn_cancelled_at is null and active is true
      and ( extract(dow from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) in (5,6)
         or extract(hour from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) not between 10 and 17
         or extract(minute from start) <> 0 or extract(second from start) <> 0 )")
  if [ "$BOGUS" != "0" ]; then
    echo "!! $SLUG has $BOGUS booking(s) on a closed day, closed hour or off-grid minute."
    exit 1
  fi

  # The second and third gates: without both of these the capture writes nothing
  # and /loadtest/otp is a 404, so a copy of this module reaching a database by
  # accident is inert.
  psql -d "$SLUG" -q <<SQL
insert into ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
values ('modryn.loadtest.enabled', '1', 1, 1, now(), now()),
       ('modryn.loadtest.secret', '$SECRET', 1, 1, now(), now())
on conflict (key) do update set value = excluded.value, write_date = now();
SQL
done

# ------------------------------------------------------------------ manifest
echo
echo "==> writing $TENANTS_JSON"
# THE FLEET ORIGIN, written once and rebuilt per tenant by guardTenants().
#
# Dev default is the laptop: http://localtest.me:8069. For a ramp that has to
# measure THROUGH NGINX — which launch gates 9 and 10 both require — export
# BASE_SCHEME=https and BASE_DOMAIN=$DOMAIN, and the fleet is served at
# https://<slug>.$DOMAIN like any boutique.
#
# That is precisely why moving off localtest.me cost the guard its "this cannot
# be a boutique" argument, and why main.js's setup() now also calls
# /loadtest/ping on every tenant. Shape is forgeable; a staging-only addon on a
# path production does not have is not.
BASE_SCHEME="${BASE_SCHEME:-http}"
BASE_DOMAIN="${BASE_DOMAIN:-localtest.me}"
if [ "$BASE_SCHEME" = https ]; then ORIGIN="https://$BASE_DOMAIN"; else ORIGIN="http://$BASE_DOMAIN:$PORT"; fi

python3 - "$TENANTS_JSON" "$SECRET" "$ORIGIN" "$MANAGERS" "$STAFF" "${SLUGS[@]}" <<'PY'
import json, subprocess, sys

out, secret, origin, managers, staff = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
slugs = sys.argv[6:]

# Logins are generated by the same rule seed_tenant.py uses. They repeat across
# tenants because res.users.login is unique per DATABASE, and one database per
# boutique is the tenancy model.
logins = ([{'login': 'owner', 'level': 'owner'}]
          + [{'login': 'mgr%d' % n, 'level': 'manager'} for n in range(1, managers + 1)]
          + [{'login': 'staff%02d' % n, 'level': 'staff'} for n in range(1, staff + 1)])

tenants = []
for slug in slugs:
    index = slug[-2:]
    ids = subprocess.run(
        ['psql', '-d', slug, '-tAc',
         "select id from product_template where is_published order by id"],
        capture_output=True, text=True, check=True).stdout.split()
    if not ids:
        raise SystemExit("!! %s has no published dresses; seeding did not commit" % slug)
    scheme, _, host = origin.partition('://')
    tenants.append({
        'slug': slug,
        'baseUrl': '%s://%s.%s' % (scheme, slug, host),
        'staff': logins,
        'dressIds': [int(i) for i in ids],
        # The harness appends a 4-digit VU index: +97252 TT VVVV.
        'phonePrefix': '+97252%s' % index,
    })

with open(out, 'w') as fh:
    json.dump({'origin': origin, 'tenants': tenants, 'loadtestSecret': secret}, fh, indent=2)
    fh.write('\n')
print('  %d tenant(s)' % len(tenants))
PY

echo
echo "======================================================================"
echo "Tenants ready. Restart with BOTH settings on the command line:"
echo
# Two settings, two different reasons:
#
#   addons-path  loads modryn_loadtest, without which /loadtest/otp 404s and the
#                customer journey cannot sign in.
#   -d           bounds the cron enumerator. A tenant missing from it is served
#                over HTTP just fine (dbfilter routes that) and then silently
#                never drains its SMS outbox and never fires a reminder — the
#                failure looks like a slow queue, not a configuration mistake.
#
# NOT as `db_name` in odoo.conf, which is what this message used to print.
# reset_tenants.sh and make_gold.sh both read odoo.conf's db_name as the list of
# LIVE tenants they must refuse to drop, so writing the load tenants into it
# makes the reset workflow refuse them:
#     !! REFUSING: 'lt01' is a LIVE tenant (listed in db_name in .../odoo.conf).
# `-d` gives the server the identical list without redefining what "live" means.
echo "  ./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1 \\"
echo "    --addons-path=\"\$PWD/odoo/addons,\$PWD/addons,$LOADTEST_ADDONS\" \\"
echo "    -d $(psql -d postgres -tAc "select string_agg(datname, ',' order by datname) from pg_database where datname in ('modryn_template','bella','noga')"),$(IFS=,; echo "${SLUGS[*]}")"
echo
echo "  (do NOT put the load tenants in odoo.conf's db_name — the reset scripts"
echo "   read that list as 'live tenants, refuse to drop'.)"
echo "======================================================================"
