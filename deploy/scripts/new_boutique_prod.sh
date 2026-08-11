#!/usr/bin/env bash
# Provision one boutique tenant IN PRODUCTION: a full PostgreSQL database cloned
# from the template, its filestore, the per-tenant fixups a raw `createdb -T`
# does not do for you, and the two pieces of platform plumbing the dev script
# has no equivalent of — the nginx tenant map and Odoo's db_name list.
#
#   sudo /opt/modryn/deploy/scripts/new_boutique_prod.sh bella "Bella Bridal"
#
# This is the production twin of scripts/new_boutique.sh. Every guard in that
# script is reproduced here for the same reason it exists there; the additions
# are marked PROD. If you change one script, read the other.
set -euo pipefail

SLUG="${1:?usage: new_boutique_prod.sh <slug> \"<Display Name>\"}"
NAME="${2:?usage: new_boutique_prod.sh <slug> \"<Display Name>\"}"

APP_ROOT=/opt/modryn
DATA_DIR=/var/lib/modryn
ODOO_CONF=/etc/odoo/odoo.conf
ENV_FILE=/etc/modryn/deploy.env
SERVICE_USER=odoo
TEMPLATE=modryn_template
FILESTORE="$DATA_DIR/filestore"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

# Is $1 an element of the db_name list in $ODOO_CONF?
#
# Deliberately NOT a regex. `grep -E 'db_name=.*(^|[=,])bella(,|$)'` looks
# obvious and is wrong twice over: GNU grep never matches a mid-pattern `^`, so
# it MISSES the first entry (which follows "= ", not "=" or ","), while BSD grep
# treats it as a zero-width always-match, so the group collapses and "ella"
# matches "bella". Splitting the list and comparing elements has neither
# failure and is shorter.
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

[ "$(id -u)" = 0 ] || die "run as root (edits /etc/odoo/odoo.conf and reloads nginx)"

# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] || die "missing $ENV_FILE — run provision.sh first"
set -a; . "$ENV_FILE"; set +a
[ -n "${DOMAIN:-}" ] || die "DOMAIN is empty in $ENV_FILE"

# The slug becomes a hostname label AND a database name AND part of an nginx
# server_name regex. Keep it strict, and keep it identical to the pattern in
# deploy/nginx/modryn-site.conf — a slug this accepts but that regex rejects
# produces a database nothing can reach.
if ! [[ "$SLUG" =~ ^[a-z][a-z0-9-]{1,30}$ ]]; then
  die "slug must be lowercase alnum/dash, starting with a letter, 2-31 chars"
fi

# PROD: reserved names. modryn_template is not a tenant, and a tenant called
# "www" or "api" would shadow a hostname we may want for something else.
case "$SLUG" in
  modryn_template|www|api|admin|mail|static|acme) die "'$SLUG' is reserved" ;;
esac

if sudo -u postgres psql -tAc "select 1 from pg_database where datname='$SLUG'" | grep -q 1; then
  die "database '$SLUG' already exists"
fi

# `createdb -T` requires ZERO open connections to the source database. In
# production the running server holds none, because odoo.conf deliberately keeps
# modryn_template OUT of db_name — that omission is what makes provisioning a
# boutique a zero-downtime operation. If this check fires, something put the
# template back in db_name (or a psql session is still open), and the fix is
# that, not a server restart.
CONNS=$(sudo -u postgres psql -tAc "select count(*) from pg_stat_activity where datname='$TEMPLATE'")
if [ "$CONNS" != "0" ]; then
  die "$CONNS connection(s) open to $TEMPLATE. Check that db_name in $ODOO_CONF does NOT list it, then close any psql sessions."
fi

# ---------------------------------------------------------------------------
say "cloning database $TEMPLATE -> $SLUG"
# ---------------------------------------------------------------------------
# As the odoo role: it has CREATEDB, and the clone must be OWNED by it. Doing
# this as postgres would leave a database the application cannot fully manage.
sudo -u "$SERVICE_USER" createdb -T "$TEMPLATE" "$SLUG"

# A clone inherits exactly what the template had, and a template built before
# the modryn addons were added produces a tenant that 404s on /book and /floor —
# while the missing unique indexes announce NOTHING AT ALL until two brides hold
# the same hour. Check the CLONE, not the template: this is the last point where
# the answer is still about THIS tenant, and the last point where undoing it is
# a single dropdb.
for idx in calendar_event_modryn_one_live_booking_per_slot \
           modryn_day_waitlist_modryn_one_offer_per_day; do
  if ! sudo -u postgres psql -d "$SLUG" -tAc "select to_regclass('$idx')" | grep -q .; then
    echo "!! $SLUG cloned from a $TEMPLATE that has no $idx." >&2
    echo "   Rebuild it: sudo -u postgres dropdb $TEMPLATE && $DEPLOY_DIR/scripts/build_template_prod.sh" >&2
    sudo -u "$SERVICE_USER" dropdb "$SLUG"
    exit 1
  fi
done

# Attachments live on disk in a directory named after the database. A cloned DB
# points at filestore rows that would otherwise resolve to nothing — every dress
# photograph would 404 with the database looking perfectly healthy.
if [ -d "$FILESTORE/$TEMPLATE" ]; then
  say "copying filestore"
  cp -R "$FILESTORE/$TEMPLATE" "$FILESTORE/$SLUG"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$FILESTORE/$SLUG"
  # nginx serves these bytes via X-Accel-Redirect and needs traverse+read.
  setfacl -R  -m u:www-data:rX "$FILESTORE/$SLUG"
  setfacl -Rd -m u:www-data:rX "$FILESTORE/$SLUG"
fi

# ---------------------------------------------------------------------------
say "applying per-tenant fixups"
# ---------------------------------------------------------------------------
BASE_URL="https://$SLUG.$DOMAIN"
sudo -u "$SERVICE_USER" \
  PATH="$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin" TZ=UTC LANG=C.UTF-8 \
  MODRYN_NAME="$NAME" MODRYN_BASE="$BASE_URL" \
  "$APP_ROOT/.venv/bin/python" "$APP_ROOT/odoo/odoo-bin" shell \
    -c "$ODOO_CONF" -d "$SLUG" --db-filter="^$SLUG\$" --no-http <<'PY'
import os, uuid
name = os.environ['MODRYN_NAME']
base = os.environ['MODRYN_BASE']

ICP = env['ir.config_parameter'].sudo()

# A duplicated database.uuid makes two tenants look like ONE instance to cron
# bookkeeping and to Odoo's publisher warranty. Always regenerate — this is the
# single fixup whose absence is invisible until two boutiques interfere.
ICP.set_param('database.uuid', str(uuid.uuid4()))

ICP.set_param('web.base.url', base)
# Otherwise the first login overwrites base.url with whatever host was used —
# including a health check hitting 127.0.0.1, which then goes out in every
# booking-confirmation SMS link.
ICP.set_param('web.base.url.freeze', 'True')

env.company.name = name
site = env['website'].search([], limit=1)
site.write({'name': name, 'domain': base})

env.cr.commit()
print('TENANT_READY %s -> %s' % (name, base))
PY

# ---------------------------------------------------------------------------
say "configuring Twilio for $SLUG"
# ---------------------------------------------------------------------------
# Credentials come from /etc/modryn/deploy.env (0600 root:root), never the repo.
# With none present the sender LOGS the message and returns success rather than
# raising, so a tenant provisioned without credentials silently texts nobody
# instead of 500ing. Deliberate — and worth knowing before someone reads
# "logged" as "delivered".
sudo -u "$SERVICE_USER" \
  PATH="$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin" TZ=UTC LANG=C.UTF-8 \
  TWILIO_ACCOUNT_SID="${TWILIO_ACCOUNT_SID:-}" \
  TWILIO_API_KEY_SID="${TWILIO_API_KEY_SID:-}" \
  TWILIO_API_KEY_SECRET="${TWILIO_API_KEY_SECRET:-}" \
  TWILIO_FROM_NUMBER="${TWILIO_FROM_NUMBER:-}" \
  "$APP_ROOT/.venv/bin/python" "$APP_ROOT/odoo/odoo-bin" shell \
    -c "$ODOO_CONF" -d "$SLUG" --db-filter="^$SLUG\$" --no-http \
    < "$APP_ROOT/scripts/configure_twilio.py"

# ---------------------------------------------------------------------------
say "PROD: registering $SLUG with Odoo and nginx"
# ---------------------------------------------------------------------------
# db_name is what BOUNDS the cron enumerator: cron_database_list() is
# `config['db_name'] or list_dbs(True)`, so a tenant missing from this line gets
# NO crons at all — no SOS escalation, no SMS outbox drain, no reminders — while
# serving pages perfectly, because dbfilter routes HTTP independently. That
# asymmetry is exactly why this edit is scripted and not left to memory.
if db_name_contains "$SLUG"; then
  say "$SLUG already in db_name"
else
  cp -a "$ODOO_CONF" "$ODOO_CONF.bak.$(date -u +%Y%m%d%H%M%S)"
  # Only the FIRST uncommented db_name line, matching how Odoo's own
  # ConfigParser reads the file. The `;`-commented paragraphs above it mention
  # db_name repeatedly and must not be touched — they are the reason the line
  # is what it is.
  #
  # The empty case is not hypothetical: odoo.conf.prod SHIPS with `db_name =`
  # empty, so the very first boutique on a box takes this branch. Appending
  # ",bella" to an empty value yields ",bella", which Odoo splits into ['',
  # 'bella'] and then tries to open a database named "" on every cron pass.
  awk -v slug="$SLUG" '
    !done && /^[[:space:]]*db_name[[:space:]]*=/ {
      sub(/[[:space:]]*$/, "")
      value = $0; sub(/^[^=]*=[[:space:]]*/, "", value)
      if (value == "") { print "db_name = " slug } else { print $0 "," slug }
      done = 1; next
    }
    { print }
  ' "$ODOO_CONF" > "$ODOO_CONF.new"
  # install(1) rather than mv: keeps 0640 root:odoo instead of inheriting the
  # temp file's mode, and this file holds admin_passwd.
  install -m 0640 -o root -g "$SERVICE_USER" "$ODOO_CONF.new" "$ODOO_CONF"
  rm -f "$ODOO_CONF.new"
  # Re-read the installed file. Checking the temp copy would only prove awk ran;
  # this proves the tenant is in the file Odoo will actually parse.
  db_name_contains "$SLUG" || die "failed to append $SLUG to db_name — restore from $ODOO_CONF.bak.*"
fi

# tenants.map is DERIVED from the db_name line we just edited, so nginx and the
# cron enumerator cannot disagree about which tenants exist.
"$DEPLOY_DIR/scripts/gen_tenants_map.sh"
nginx -t
systemctl reload nginx

# Odoo parses db_name ONCE, at startup. A reload is not enough and there is no
# signal that re-reads it: until the service restarts, this tenant serves pages
# but runs no crons, so the 1-minute SOS escalation never fires for it. Restart
# is therefore the default, not an afterthought. Prefork drains in-flight
# requests within TimeoutStopSec, so this is seconds of 502, not an outage —
# but it is not nothing, which is why --no-restart exists for batching.
if [ "${MODRYN_NO_RESTART:-0}" = 1 ]; then
  printf '\033[33m!!\033[0m MODRYN_NO_RESTART set — %s has NO CRONS until you run: systemctl restart odoo\n' "$SLUG"
else
  say "restarting odoo so the cron enumerator picks up $SLUG"
  systemctl restart odoo
fi

echo
echo "Provisioned: $BASE_URL"
echo "Verify:      curl -sI $BASE_URL/shop | head -1"
echo "Seed staff:  see deploy/README.md 'Add a boutique', step 6"
