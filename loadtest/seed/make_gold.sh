#!/usr/bin/env bash
# Freeze one fully-seeded load tenant as the reset source.
#
#   ./loadtest/seed/make_gold.sh lt01
#
# Taken from a real tenant rather than rebuilt, so the reset source is provably
# identical to what the first stage actually ran against. Run it AFTER
# gen_tenants.sh and BEFORE the first run — a gold made after a stage carries
# that stage's bookings, and every later stage then starts from a different
# board than the one it is being compared to.
#
# THE ODOO SERVER MUST BE STOPPED: `createdb -T` demands zero connections to the
# source, and the source here is a served tenant.
set -euo pipefail

SOURCE="${1:?usage: make_gold.sh <slug>   e.g. make_gold.sh lt01}"
GOLD="${MODRYN_GOLD:-modryn_gold_seeded}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
FILESTORE="$REPO/.odoo-data/filestore"

# MODRYN_GOLD is an env override, so `MODRYN_GOLD=bella make_gold.sh lt01` used to
# drop the live boutique and its filestore two lines below. Element-wise against
# odoo.conf's db_name, not grep — a regex form misses the first list entry under
# GNU grep and matches 'ella' inside 'bella' under BSD grep, which turns the
# refusal into a no-op exactly when it matters. Mirrors reset_tenants.sh.
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
if db_name_contains "$GOLD"; then
  echo "!! REFUSING: gold target '$GOLD' is a LIVE tenant (db_name in $ODOO_CONF)."
  echo "   This script drops it. Unset or correct MODRYN_GOLD."
  exit 1
fi

if ! psql -d postgres -tAc "select 1 from pg_database where datname='$SOURCE'" | grep -q 1; then
  echo "!! no such database: $SOURCE"
  exit 1
fi

CONNS=$(psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='$SOURCE'")
if [ "$CONNS" != "0" ]; then
  echo "!! $CONNS connection(s) open to $SOURCE — stop the Odoo server first"
  exit 1
fi

# A gold that predates the capture addon produces tenants whose customer journey
# cannot read a code back, and the symptom is a login failure in k6 rather than a
# missing module. Say it here, where the answer is still cheap.
if [ "$(psql -d "$SOURCE" -tAc "select count(*) from ir_module_module where name='modryn_loadtest' and state='installed'")" != "1" ]; then
  echo "!! $SOURCE does not have modryn_loadtest installed — re-run gen_tenants.sh"
  exit 1
fi

echo "==> $SOURCE -> $GOLD"
dropdb --if-exists "$GOLD"
createdb -T "$SOURCE" "$GOLD"

rm -rf "${FILESTORE:?}/$GOLD"
if [ -d "$FILESTORE/$SOURCE" ]; then
  cp -R "$FILESTORE/$SOURCE" "$FILESTORE/$GOLD"
fi

# Every seeded phone in the gold carries the SOURCE tenant's two-digit index, and
# a restored tenant would answer for numbers tenants.json says belong to someone
# else — so /my/bookings comes back empty for the VU that seeded it. reset_tenants.sh
# renumbers them, and this is how it learns which prefix to replace.
psql -d "$GOLD" -q -c "
insert into ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
values ('modryn.loadtest.gold_source', '$SOURCE', 1, 1, now(), now())
on conflict (key) do update set value = excluded.value;"

echo
echo "Gold: $GOLD (never add it to db_name or dbfilter — a connection to it is"
echo "what makes the next createdb -T fail)."
echo "Reset with: ./loadtest/seed/reset_tenants.sh lt01 lt02 ..."
