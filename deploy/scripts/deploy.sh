#!/usr/bin/env bash
# Ship a commit to production.
#
#   sudo /opt/modryn/deploy/scripts/deploy.sh <git-ref>
#
# Deliberately boring: fetch, checkout, install deps, upgrade every tenant,
# restart, reload nginx, verify. It exits non-zero if the regression suite
# reports a single failure, so "the deploy went fine" and "the suite is green"
# cannot become two different claims.
set -euo pipefail

APP_ROOT=/opt/modryn
ODOO_CONF=/etc/odoo/odoo.conf
SERVICE_USER=odoo
ENV_FILE=/etc/modryn/deploy.env
DEPLOY_DIR="$APP_ROOT/deploy"

# DOMAIN, for pointing the regression suite at this box's real hostnames.
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

REF="${1:?usage: deploy.sh <git-ref>}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"

PREV="$(git -C "$APP_ROOT" rev-parse HEAD)"
say "current commit $PREV — this is what to roll back to"

# A deploy that starts without a fresh backup is a deploy with no way back from
# a bad migration. The status file is written by backup.sh every night.
if [ -f /var/log/modryn-backup.status ]; then
  printf '    last backup: %s\n' "$(cat /var/log/modryn-backup.status)"
  grep -q '^OK' /var/log/modryn-backup.status || die "last backup FAILED — fix it before upgrading schemas"
else
  die "no /var/log/modryn-backup.status — run backup.sh before deploying"
fi

say "fetching $REF"
git -C "$APP_ROOT" fetch --all --tags
git -C "$APP_ROOT" checkout --detach "$REF"
NEW="$(git -C "$APP_ROOT" rev-parse HEAD)"

say "python requirements"
"$APP_ROOT/.venv/bin/pip" install -q -r "$APP_ROOT/odoo/requirements.txt"
"$APP_ROOT/.venv/bin/pip" install -q rlPyCairo

# ---------------------------------------------------------------------------
say "stopping odoo for the upgrade window"
# ---------------------------------------------------------------------------
# `-u all` writes schema. Running it while the service is serving means two
# processes hold the same registry through a migration, which is how a half-
# upgraded tenant happens.
systemctl stop odoo

# ---------------------------------------------------------------------------
# Module upgrades are a LOOP over N databases, one at a time, with the platform
# down for the whole loop. At two tenants that is a minute. At fifty it is the
# reason a cross-tenant migration console eventually becomes someone's job.
#
# --no-http on every invocation. `systemctl stop odoo` above should have freed
# 8069, but PreforkServer.start() binds it BEFORE loading any registry, so if
# the stop ever fails or a second deploy overlaps, the whole loop dies at
# socket.bind() with EADDRINUSE and no tenant is upgraded. There is no reason
# for an upgrade run to listen on anything.
# Print elapsed time PER TENANT so the trend is visible long before it is a
# crisis — this number is the honest cost of database-per-tenant.
# ---------------------------------------------------------------------------
DB_LINE="$(grep -E '^[[:space:]]*db_name[[:space:]]*=' "$ODOO_CONF" | head -n 1)"
DBS="${DB_LINE#*=}"; DBS="${DBS//[[:space:]]/}"
IFS=',' read -r -a TENANTS <<< "$DBS"
# The template is upgraded too: a tenant provisioned tomorrow is a clone of it,
# and a template left on the old schema silently ships yesterday's code.
TENANTS+=("modryn_template")

TOTAL_START=$SECONDS
for db in "${TENANTS[@]}"; do
  [ -n "$db" ] || continue
  START=$SECONDS
  say "upgrading $db"
  sudo -u "$SERVICE_USER" \
    PATH="$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin" TZ=UTC LANG=C.UTF-8 \
    "$APP_ROOT/.venv/bin/python" "$APP_ROOT/odoo/odoo-bin" server \
      -c "$ODOO_CONF" -d "$db" --db-filter="^$db\$" \
      -u all --no-http --stop-after-init \
    || die "upgrade FAILED for $db — platform is stopped, roll back: deploy.sh $PREV"
  printf '    %s upgraded in %ss\n' "$db" "$((SECONDS - START))"
done
printf '    all tenants upgraded in %ss\n' "$((SECONDS - TOTAL_START))"

# ---------------------------------------------------------------------------
say "restarting"
# ---------------------------------------------------------------------------
systemctl start odoo

"$DEPLOY_DIR/scripts/gen_tenants_map.sh"
nginx -t
systemctl reload nginx

# ---------------------------------------------------------------------------
say "regression suite"
# ---------------------------------------------------------------------------
# The suite runs THROUGH NGINX, against this box's real hostnames. It used to
# address bella.localtest.me / noga.localtest.me via an /etc/hosts entry, which
# meant the deploy gate exercised Odoo and never touched the thing actually
# serving customers — TLS, the catch-all, the rate limits and the
# X-Forwarded-Host that ProxyFix is gated on were all outside the gate. The
# nginx-specific checks in verify_edge.sh are now an ADDITION rather than a
# compensation, and both are run below.
#
# Four variables, none optional:
#   BASE_HOST/BASE_SCHEME  the hostnames to address, and no ":443" in the URL
#   ODOO_CONF              /etc/odoo/odoo.conf, NOT the ./odoo.conf that also
#                          exists in this checkout and lists a laptop's tenants
#   MODRYN_DEMO_PASSWORD   or section 10a fails rather than skips
#
# As SERVICE_USER, not root: the suite makes 72 `psql -d <tenant>` calls that
# authenticate by peer, and there is no `root` PostgreSQL role. Run as root they
# would every one return empty through the `2>/dev/null || echo 0` idiom and be
# read as legitimate zeros — the whole suite green, having asserted nothing.
if [ -x "$APP_ROOT/scripts/verify.sh" ]; then
  if [ -z "${MODRYN_DEMO_PASSWORD:-}" ]; then
    printf '\033[33m!!\033[0m MODRYN_DEMO_PASSWORD unset — the sign-in section will FAIL, not skip\n'
  fi
  [ -n "${DOMAIN:-}" ] || die "DOMAIN unset — $ENV_FILE is unreadable, and the suite would silently fall back to localtest.me and test nothing on this box"
  sudo -u "$SERVICE_USER" env \
    BASE_HOST="$DOMAIN" BASE_SCHEME=https ODOO_CONF="$ODOO_CONF" \
    MODRYN_DEMO_PASSWORD="${MODRYN_DEMO_PASSWORD:-}" \
    "$APP_ROOT/scripts/verify.sh" \
    || die "verify.sh reported failures on $NEW — roll back: deploy.sh $PREV"
else
  die "no $APP_ROOT/scripts/verify.sh — refusing to call this deploy verified"
fi

# The other half. verify.sh proves Odoo is correct for hostnames that exist;
# this proves nginx is correct for the ones that must NOT. Neither is a superset
# of the other, and a deploy that reloads nginx (above) without re-checking it
# is a deploy that can serve the database manager and still report green.
if [ -x "$DEPLOY_DIR/scripts/verify_edge.sh" ]; then
  "$DEPLOY_DIR/scripts/verify_edge.sh" \
    || die "verify_edge.sh reported failures on $NEW — roll back: deploy.sh $PREV"
else
  die "no $DEPLOY_DIR/scripts/verify_edge.sh — refusing to call this deploy verified"
fi

echo
echo "Deployed $NEW (was $PREV)."
echo "Roll back with: sudo $0 $PREV"
