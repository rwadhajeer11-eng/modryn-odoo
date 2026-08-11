#!/usr/bin/env bash
# Restore one tenant from a backup set.
#
#   sudo /opt/modryn/deploy/scripts/restore.sh <tenant> <backup-set-dir> [--as <name>] [--force]
#
# e.g. sudo .../restore.sh bella /var/backups/modryn/daily/2026-08-09
#
# BACKUPS EXIST TO BE RESTORED. The only evidence a backup works is a restore,
# which is why this script ends by CHECKING the result rather than announcing
# success, and stops short of promoting anything. A human decides that.
#
# By default it restores to "<tenant>_restore", NEVER over the live database.
# --force is required to target a database that already exists, and it still
# refuses if that database is the one currently being served.
set -euo pipefail

APP_ROOT=/opt/modryn
DATA_DIR=/var/lib/modryn
ODOO_CONF=/etc/odoo/odoo.conf
ENV_FILE=/etc/modryn/deploy.env
SERVICE_USER=odoo
FILESTORE="$DATA_DIR/filestore"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

TENANT="${1:-}"; SET_DIR="${2:-}"
[ -n "$TENANT" ] && [ -n "$SET_DIR" ] || die "usage: restore.sh <tenant> <backup-set-dir> [--as <name>] [--force]"
shift 2

TARGET="${TENANT}_restore"
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --as)    TARGET="${2:?--as needs a name}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *)       die "unknown argument: $1" ;;
  esac
done

[ "$(id -u)" = 0 ] || die "run as root"
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
[ -n "${DOMAIN:-}" ] || die "DOMAIN is empty in $ENV_FILE"

DUMP="$SET_DIR/$TENANT.dump"
[ -f "$DUMP" ] || die "no dump at $DUMP"
[ -d "$SET_DIR/filestore/$TENANT" ] || say "WARNING: no filestore for $TENANT in this set — attachments will 404"

# ---------------------------------------------------------------------------
# Refuse to overwrite anything live
# ---------------------------------------------------------------------------
# Two separate refusals, because they fail differently:
#   (1) the target already exists  -> needs --force,
#   (2) the target is a SERVED tenant (listed in db_name) -> refused outright,
#       because restoring over the database that is currently taking bookings
#       replaces this morning's real appointments with last night's copy, and
#       there is no undo for that.
# Element-wise comparison, not a regex: `grep -E '(^|[=,])x(,|$)'` misses the
# first list entry under GNU grep (it follows "= ", not "=" or ",") and matches
# substrings under BSD grep. Getting this wrong here means the refusal silently
# stops refusing, which is the one failure this check exists to prevent.
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

if db_name_contains "$TARGET"; then
  die "'$TARGET' is a LIVE tenant (listed in db_name). Restore to a scratch name and promote deliberately — see deploy/README.md."
fi

EXISTS=$(sudo -u postgres psql -tAc "select 1 from pg_database where datname='$TARGET'" || true)
if [ -n "$EXISTS" ]; then
  [ "$FORCE" = 1 ] || die "database '$TARGET' already exists — pass --force to drop and recreate it"
  say "dropping existing '$TARGET' (--force)"
  sudo -u postgres dropdb "$TARGET"
fi

# ---------------------------------------------------------------------------
say "creating $TARGET and restoring"
# ---------------------------------------------------------------------------
# Same encoding/collation the original was created with. pg_restore does not
# create the database or set its collation; getting this wrong produces a
# database that restores cleanly and sorts Hebrew differently from its source.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres <<SQL
CREATE DATABASE "$TARGET" ENCODING 'unicode' LC_COLLATE 'C' TEMPLATE template0;
ALTER DATABASE "$TARGET" OWNER TO "$SERVICE_USER";
SQL
# EVERY extension the dump carries must exist before pg_restore runs as the
# non-superuser odoo role. build_template_prod.sh creates three, so every tenant
# has three and every `pg_dump -Fc` carries a CREATE EXTENSION for each.
# Measured on PostgreSQL 16:
#   select name, trusted from pg_available_extension_versions
#    where name in ('pg_trgm','unaccent','pg_stat_statements');
#   -> pg_trgm t | unaccent t | pg_stat_statements f
# pg_stat_statements is NOT trusted, so the odoo role cannot create it and the
# restore used to abort here with
#   ERROR: permission denied to create extension "pg_stat_statements"
# Omitting it from this list was not a cosmetic gap: `set -e` on the pg_restore
# below killed the script before the filestore copy and before every
# verification check, i.e. the failure looked like a bad dump.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$TARGET" \
  -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements' >/dev/null
# The two trusted ones are created BY the odoo role, so it OWNS them — see the
# COMMENT ON EXTENSION note below.
sudo -u "$SERVICE_USER" psql -v ON_ERROR_STOP=1 -d "$TARGET" \
  -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm' -c 'CREATE EXTENSION IF NOT EXISTS unaccent' >/dev/null

# -j 4: the dumps are ~80 MB, so this is minutes vs. minutes — but the same flag
# is what makes a 20-tenant recovery finish inside a morning.
# --no-owner because the dump was taken --no-owner; roles are the target box's
# business, not the backup's.
#
# pg_restore CANNOT exit 0 here and that is not a fault in the backup. pg_dump
# emits `COMMENT ON EXTENSION <name>` for each extension, and COMMENT requires
# OWNERSHIP. pg_stat_statements must be owned by postgres (not trusted, and
# there is no ALTER EXTENSION ... OWNER TO), so its comment always fails and
# pg_restore reports "errors ignored on restore: 1" and exits 1. Measured on the
# dev instance with a real -Fc dump:
#   all three extensions pre-created as postgres  -> 3 errors, exit 1
#   trusted two owned by the restoring role       -> 1 error,  exit 1
# So: never `set -e` on this call. Instead, classify. A COMMENT ON EXTENSION
# error is expected; ANY other error means data did not land and must fail the
# restore, which is why the count is compared rather than the exit status
# ignored wholesale.
RESTORE_LOG="$(mktemp)"
sudo -u "$SERVICE_USER" pg_restore -d "$TARGET" -j 4 --no-owner "$DUMP" 2>"$RESTORE_LOG" || true
ERRORS=$(grep -c '^pg_restore: error:' "$RESTORE_LOG" || true)
EXPECTED=$(grep -c '^Command was: COMMENT ON EXTENSION' "$RESTORE_LOG" || true)
UNEXPECTED=$(( ERRORS - EXPECTED ))
if [ "$UNEXPECTED" -gt 0 ]; then
  say "pg_restore reported $ERRORS error(s), $UNEXPECTED of them NOT an extension comment:"
  grep -v '^$' "$RESTORE_LOG" >&2 || true
  RESTORE_UNEXPECTED=1
else
  say "pg_restore: $ERRORS ignorable error(s) (COMMENT ON EXTENSION, expected)"
  RESTORE_UNEXPECTED=0
fi
rm -f "$RESTORE_LOG"

# ---------------------------------------------------------------------------
say "restoring filestore"
# ---------------------------------------------------------------------------
if [ -d "$SET_DIR/filestore/$TENANT" ]; then
  rm -rf "${FILESTORE:?}/${TARGET:?}"
  cp -R "$SET_DIR/filestore/$TENANT" "$FILESTORE/$TARGET"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$FILESTORE/$TARGET"
  setfacl -R  -m u:www-data:rX "$FILESTORE/$TARGET"
  setfacl -Rd -m u:www-data:rX "$FILESTORE/$TARGET"
fi

# ---------------------------------------------------------------------------
say "applying the same per-tenant fixups new_boutique_prod.sh applies"
# ---------------------------------------------------------------------------
# A restored copy running ALONGSIDE the original is exactly the "two tenants
# believing they are the same instance" case those fixups were written to
# prevent: a duplicated database.uuid confuses cron bookkeeping, and an
# unfrozen web.base.url means the restored copy starts sending links to the
# LIVE hostname — so a test restore can put wrong URLs into real SMS.
sudo -u "$SERVICE_USER" \
  PATH="$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin" TZ=UTC LANG=C.UTF-8 \
  MODRYN_BASE="https://$TARGET.$DOMAIN" \
  "$APP_ROOT/.venv/bin/python" "$APP_ROOT/odoo/odoo-bin" shell \
    -c "$ODOO_CONF" -d "$TARGET" --db-filter="^$TARGET\$" --no-http <<'PY'
import os, uuid
base = os.environ['MODRYN_BASE']
ICP = env['ir.config_parameter'].sudo()
ICP.set_param('database.uuid', str(uuid.uuid4()))
ICP.set_param('web.base.url', base)
ICP.set_param('web.base.url.freeze', 'True')
site = env['website'].search([], limit=1)
if site:
    site.domain = base
env.cr.commit()
print('RESTORE_FIXUPS_APPLIED %s' % base)
PY

# ---------------------------------------------------------------------------
say "verifying the restore"
# ---------------------------------------------------------------------------
# Verify, do not assume. Each check below has actually been the thing that was
# wrong in some restore somewhere; "pg_restore exited 0" is not one of them.
#
# TWO KINDS OF CHECK, because a row count of 0 means two different things.
# A single check() that failed on "0" made an honest restore of a boutique that
# has taken no bookings yet read as a corrupt backup — and it did so on the
# operator's FIRST drill, which is the worst possible moment to teach someone
# that the verifier cries wolf.
RC=0

# The query must succeed AND return something meaningfully non-empty: an
# existing index, a positive count, a true boolean. Use this only where zero
# genuinely means the restore lost something.
check() {
  local label="$1" sql="$2"
  local out
  out="$(sudo -u postgres psql -d "$TARGET" -tAc "$sql" 2>/dev/null || echo '')"
  if [ -n "$out" ] && [ "$out" != "0" ] && [ "$out" != "f" ]; then
    printf '  \033[32mPASS\033[0m %-46s %s\n' "$label" "$out"
  else
    printf '  \033[31mFAIL\033[0m %-46s %s\n' "$label" "${out:-<empty>}"
    RC=1
  fi
}

# The TABLE must be present and readable; the count is reported, never judged.
# psql exits non-zero and prints nothing when the relation is missing, which is
# the actual corruption signal for these.
check_readable() {
  local label="$1" table="$2"
  local out
  if out="$(sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$TARGET" -tAc "select count(*) from $table" 2>/dev/null)" \
     && [ -n "$out" ]; then
    printf '  \033[32mPASS\033[0m %-46s %s row(s)\n' "$label" "$out"
  else
    printf '  \033[31mFAIL\033[0m %-46s table missing or unreadable\n' "$label"
    RC=1
  fi
}

# The uniqueness indexes are the checks that matter most: they are the only
# thing standing between two brides and the same fitting room, and a dump/restore
# is one of the few operations that can plausibly lose an index without losing
# a row. to_regclass returns NULL — an empty field — when the index is gone.
check "index one_live_booking_per_slot"  "select to_regclass('calendar_event_modryn_one_live_booking_per_slot') is not null"
check "index one_offer_per_day"          "select to_regclass('modryn_day_waitlist_modryn_one_offer_per_day') is not null"

# Non-empty REQUIRED. A tenant with zero installed modryn modules is not a
# boutique; res_users and res_partner are never empty in any Odoo database
# (base data ships __system__, the public user and their partners), so zero
# there means the restore dropped rows it should have had.
check "modryn modules installed"         "select count(*) from ir_module_module where name like 'modryn_%' and state='installed'"
check "res_users rows"                   "select count(*) from res_users"
check "res_partner rows"                 "select count(*) from res_partner"
check "database.uuid regenerated"        "select (value is not null and value <> '') from ir_config_parameter where key='database.uuid'"

# Zero is LEGITIMATE here, so only presence is asserted.
#   calendar_event  — a boutique that opened this week has no appointments yet.
#   ir_attachment   — a fresh tenant carries no dress photographs, and Odoo
#                     builds its asset bundles lazily on first page render, so
#                     an un-browsed restore can hold none of those either.
# The filestore cross-check below is what actually catches a half-restore of
# attachments, and it is conditional on there being attachment rows at all.
check_readable "calendar_event readable"  calendar_event
check_readable "ir_attachment readable"   ir_attachment

# Attachments stored on disk: count the rows that expect a file and make sure
# the filestore directory is not empty. A database that restores perfectly while
# its filestore did not is the single most common half-restore.
STORED=$(sudo -u postgres psql -d "$TARGET" -tAc "select count(*) from ir_attachment where store_fname is not null" 2>/dev/null || echo 0)
FILES=$(find "$FILESTORE/$TARGET" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$STORED" -gt 0 ] && [ "$FILES" = "0" ]; then
  printf '  \033[31mFAIL\033[0m %-46s %s rows expect files, filestore is empty\n' "filestore present" "$STORED"
  RC=1
else
  printf '  \033[32mPASS\033[0m %-46s %s rows / %s files\n' "filestore present" "$STORED" "$FILES"
fi

echo
if [ "$RESTORE_UNEXPECTED" = 1 ]; then
  die "pg_restore reported errors other than extension comments — do not promote '$TARGET'"
fi
if [ "$RC" != 0 ]; then
  die "restore completed with FAILED checks above — do not promote '$TARGET'"
fi

cat <<EOF
Restored to database '$TARGET'. NOT promoted, NOT served — no nginx or db_name
change has been made, deliberately.

To exercise it end to end (the drill):
  1. add '$TARGET' to db_name in $ODOO_CONF
  2. /opt/modryn/deploy/scripts/gen_tenants_map.sh && nginx -t && systemctl reload nginx
  3. systemctl restart odoo
  4. curl -sI https://$TARGET.$DOMAIN/shop | head -1        # expect 200
  5. record the date and result in the restore drill log (deploy/README.md)

To promote it over the original — a decision, not a step:
  read deploy/README.md "Restore a tenant" before doing this.

To discard it:
  sudo -u postgres dropdb $TARGET && rm -rf $FILESTORE/$TARGET
EOF
