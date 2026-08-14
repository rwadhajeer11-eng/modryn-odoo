#!/usr/bin/env bash
# One-time migration: delete every tenant's private COPY of the Twilio
# credentials, so the four TWILIO_* variables in the Odoo process environment
# become the single source for the whole platform.
#
#   ./scripts/migrate_twilio_to_platform.sh            # DRY RUN — reports only
#   ./scripts/migrate_twilio_to_platform.sh --apply    # actually deletes
#
# On the box, as the SERVICE USER — the psql calls below authenticate by peer
# and there is no `root` PostgreSQL role, so run as root every count comes back
# empty and this script cheerfully reports nothing to migrate (the same reason
# deploy.sh runs verify.sh that way):
#
#   sudo -u odoo env ODOO_CONF=/etc/odoo/odoo.conf \
#     /opt/modryn/scripts/migrate_twilio_to_platform.sh --apply
#
# WHY: configure_twilio.py used to write modryn.twilio.{account_sid,api_key_sid,
# api_key_secret,from_number} into every tenant. _twilio_config() prefers those
# over the environment, so while they exist a credential rotation on the
# platform changes nothing for any tenant provisioned before it — and the
# failure is silent: the boutique keeps sending through the old key right up
# until it is revoked, then stops with no configuration having changed. Two
# places to rotate is one place too many.
#
# modryn.twilio.disabled is NEVER deleted. It is not a credential, it is the
# per-tenant off switch, and dropping it would un-mute a boutique that somebody
# deliberately silenced — a QA tenant that starts texting real brides.
set -euo pipefail

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# The same trap verify.sh:50 documents: /opt/modryn and this repository are
# checkouts of the same tree, so ./odoo.conf EXISTS on the box — and it is the
# DEVELOPER's config, listing a laptop's tenants. Reading it there would
# enumerate databases that have nothing to do with what the server runs, and
# this script would report "nothing to migrate" while every real tenant kept
# its copies. Pass ODOO_CONF explicitly in production.
ODOO_CONF="${ODOO_CONF:-odoo.conf}"
P_DISABLED='modryn.twilio.disabled'

APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --apply)   APPLY=1; shift ;;
    -h|--help) die "usage: migrate_twilio_to_platform.sh [--apply]   (default: dry run)" ;;
    *)         die "unknown argument: $1" ;;
  esac
done

[ -r "$ODOO_CONF" ] || die "cannot read $ODOO_CONF (set ODOO_CONF=/etc/odoo/odoo.conf on the box)"

# Deleting the tenant copies while the platform environment holds nothing turns
# every boutique into a log-only sender, and does it QUIETLY: _send_now logs the
# body and returns success, so the first evidence is a bride who never got her
# reminder. This inspects YOUR shell, which is only a proxy for the server's —
# the unit inherits these via EnvironmentFile in deploy/systemd/odoo.service, so
# also make sure it has been restarted since that line landed.
MISSING=
for v in TWILIO_ACCOUNT_SID TWILIO_API_KEY_SID TWILIO_API_KEY_SECRET TWILIO_FROM_NUMBER; do
  [ -n "${!v:-}" ] || MISSING="$MISSING $v"
done
if [ -n "$MISSING" ]; then
  if [ "$APPLY" = 1 ]; then
    die "not set in this shell:$MISSING
   Source the platform credentials first, so you are deleting a duplicate and
   not the last copy:  set -a; . /etc/modryn/deploy.env; set +a   (dev: ./.env)
   If a tenant is MEANT to stop sending, set $P_DISABLED on it instead."
  fi
  say "WARNING — not set in this shell:$MISSING (dry run continues; --apply will refuse)"
fi

# ONE SOURCE OF TRUTH for what a tenant is, the same derivation gen_tenants_map.sh
# uses (:24-31): the FIRST uncommented db_name assignment, anchored on '^' so the
# `;`-commented explanations above it — which do contain the words db_name — are
# skipped. nginx, the cron enumerator and this script must not disagree about the
# tenant set; a database this script has never heard of keeps its copies forever.
DB_NAME_LINE="$(grep -E '^[[:space:]]*db_name[[:space:]]*=' "$ODOO_CONF" | head -n 1 || true)"
[ -n "$DB_NAME_LINE" ] || die "no db_name line in $ODOO_CONF — refusing to guess the tenant list"
DBS="${DB_NAME_LINE#*=}"
DBS="${DBS//[[:space:]]/}"

if [ "$APPLY" = 1 ]; then
  say "APPLYING — deleting per-tenant Twilio credentials listed in $ODOO_CONF"
else
  say "DRY RUN — reporting only. Re-run with --apply to delete. ($ODOO_CONF)"
fi

TOTAL=0
TENANTS=0
IFS=','
for db in $DBS; do
  [ -n "$db" ] || continue

  # modryn_template is not a tenant — gen_tenants_map.sh refuses to publish it
  # (:49-52) for the same reason. It is deliberately absent from db_name in
  # production; a dev odoo.conf lists it. Its copies are not migrated but
  # rebuilt away, because a template edited in place and a template rebuilt from
  # build_template.sh must not diverge.
  if [ "$db" = modryn_template ]; then
    say "skipping modryn_template (not a tenant — rebuild it instead: dropdb modryn_template && ./scripts/build_template.sh)"
    continue
  fi

  TENANTS=$((TENANTS + 1))

  # Keys only, never `value`: this output ends up in transcripts and in the
  # deploy log. ON_ERROR_STOP so an unreachable database fails the run instead
  # of returning empty and being read as a legitimate "already migrated".
  KEYS="$(psql -d "$db" -v ON_ERROR_STOP=1 -tAc \
    "select key from ir_config_parameter where key like 'modryn.twilio.%' and key <> '$P_DISABLED' order by key")"
  if [ -n "$KEYS" ]; then
    N=$(printf '%s\n' "$KEYS" | wc -l | tr -d ' ')
  else
    N=0
  fi

  # Presence, not value — a muted tenant stays muted, and an operator reading
  # this report should know which ones those are before wondering why they are
  # silent after the migration.
  OFF="$(psql -d "$db" -v ON_ERROR_STOP=1 -tAc \
    "select count(*) from ir_config_parameter where key = '$P_DISABLED'")"
  NOTE=
  [ "$OFF" = 0 ] || NOTE="   [$P_DISABLED set — stays muted]"

  if [ "$N" = 0 ]; then
    printf '  %-24s no credential rows%s\n' "$db" "$NOTE"
    continue
  fi
  printf '  %-24s %s row(s): %s%s\n' "$db" "$N" "$(printf '%s' "$KEYS" | tr '\n' ' ')" "$NOTE"

  if [ "$APPLY" = 1 ]; then
    psql -d "$db" -v ON_ERROR_STOP=1 -qc \
      "delete from ir_config_parameter where key like 'modryn.twilio.%' and key <> '$P_DISABLED'"
    TOTAL=$((TOTAL + N))
  fi
done
unset IFS

echo
if [ "$APPLY" = 1 ]; then
  say "deleted $TOTAL row(s) across $TENANTS tenant(s)"
  echo "Restart Odoo only if it is not already reading TWILIO_* from its environment;"
  echo "ir.config_parameter is read per request, so the deletion takes effect immediately."
else
  say "nothing changed — $TENANTS tenant(s) inspected. Re-run with --apply."
fi

cat <<EOF

RECOVERY. Nothing here is unrecoverable, because the database was never the only
copy: the credentials live in /etc/modryn/deploy.env (production, 0600 root) and
in the gitignored ./.env (development). To give ONE tenant its own copy back:

  set -a; . ./.env; set +a          # prod: . /etc/modryn/deploy.env
  ./odoo/odoo-bin shell -c odoo.conf -d <tenant> --db-filter='^<tenant>\$' --no-http \\
      < scripts/configure_twilio.py

  (on the box, run that as the odoo user with the absolute paths
   new_boutique_prod.sh uses — see the Twilio block in that script)

Do that only for a boutique that genuinely needs its OWN sender identity. To stop
a tenant sending, set $P_DISABLED on it — absent credentials are no longer proof
that a database cannot dial out.
EOF
