#!/usr/bin/env bash
# MODRYN's own database — the register of which boutiques subscribe.
#
# NOT a boutique. new_boutique.sh clones modryn_template, which carries the
# seven boutique addons; this one starts from nothing and installs only
# modryn_platform. Two reasons it must not be a clone:
#
#   * modryn_platform is deliberately absent from modryn_template, so that no
#     boutique owner can ever open a screen listing her competitors. Cloning the
#     template would give the platform every boutique model it has no use for,
#     and give a boutique nothing it needs.
#   * The platform owner is an INTERNAL user (base.group_user). Boutique staff
#     are portal users. Keeping the two in separate databases keeps that
#     distinction impossible to get wrong.
#
# Idempotent: re-run it to upgrade the module on an existing platform database.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

DB="${1:-platform}"
PORT="${PORT:-8069}"

if [ ! -d .venv ]; then
  echo "!! no .venv — run scripts/bootstrap.sh first" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# The server holds the databases open; a create or an -i against a database the
# running server has a registry for is how two processes end up disagreeing
# about the schema.
if pgrep -f "odoo-bin server" >/dev/null 2>&1; then
  echo "!! stop the Odoo server first — it holds the databases open" >&2
  exit 1
fi

if psql -d postgres -tAc "select 1 from pg_database where datname='$DB'" | grep -q 1; then
  echo "==> $DB exists — upgrading modryn_platform"
  ./odoo/odoo-bin server -c odoo.conf -d "$DB" --db-filter="^$DB\$" \
    -u modryn_platform --stop-after-init
else
  echo "==> creating $DB and installing modryn_platform"
  createdb "$DB"
  ./odoo/odoo-bin server -c odoo.conf -d "$DB" --db-filter="^$DB\$" \
    -i modryn_platform --without-demo=True --stop-after-init
fi

# The base.url has to name this database's own host, or every link Odoo builds
# points at whichever tenant happened to set it last.
MODRYN_DB="$DB" MODRYN_PORT="$PORT" ./odoo/odoo-bin shell -c odoo.conf -d "$DB" \
  --db-filter="^$DB\$" --no-http <<'PY'
import os
db = os.environ['MODRYN_DB']
port = os.environ.get('MODRYN_PORT', '8069')
env['ir.config_parameter'].sudo().set_param(
    'web.base.url', 'http://%s.localtest.me:%s' % (db, port))
# Give the admin account the platform group, so there is somebody who can open
# the register the moment this script finishes.
admin = env.ref('base.user_admin', raise_if_not_found=False)
group = env.ref('modryn_platform.group_platform_owner', raise_if_not_found=False)
if admin and group:
    admin.write({'group_ids': [(4, group.id)]})
env.cr.commit()
print('PLATFORM_READY db=%s admin_is_platform_owner=%s' % (
    db, bool(admin and group and admin.has_group('modryn_platform.group_platform_owner'))))
PY

echo
echo "Platform register: http://$DB.localtest.me:$PORT/platform/boutiques"
echo "Sign in at /web/login as admin."
