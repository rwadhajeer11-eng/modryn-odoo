#!/usr/bin/env bash
# Provision one boutique tenant: a full PostgreSQL database cloned from the
# template, plus its filestore, plus the per-tenant fixups that a raw
# `createdb -T` does NOT do for you.
#
# This script IS the tenancy-ops evidence for the scorecard: every line here is
# work that MODRYN's RLS model gets for free.
#
# Usage: ./scripts/new_boutique.sh bella "Bella Bridal"
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

# Attachments live on disk in a directory named after the database. A cloned DB
# points at filestore rows that would otherwise resolve to nothing.
if [ -d "$FILESTORE/$TEMPLATE" ]; then
  echo "==> copying filestore"
  cp -R "$FILESTORE/$TEMPLATE" "$FILESTORE/$SLUG"
fi

echo "==> applying per-tenant fixups"
MODRYN_SLUG="$SLUG" MODRYN_NAME="$NAME" MODRYN_PORT="$PORT" \
./odoo/odoo-bin shell -c odoo.conf -d "$SLUG" --db-filter="^$SLUG\$" --no-http <<'PY'
import os, uuid
slug = os.environ['MODRYN_SLUG']
name = os.environ['MODRYN_NAME']
port = os.environ['MODRYN_PORT']

ICP = env['ir.config_parameter'].sudo()

# A duplicated database.uuid makes two tenants look like one instance to cron
# bookkeeping and to Odoo's own publisher warranty. Always regenerate.
ICP.set_param('database.uuid', str(uuid.uuid4()))

base = 'http://%s.localtest.me:%s' % (slug, port)
ICP.set_param('web.base.url', base)
# Otherwise the first login overwrites base.url with whatever host was used.
ICP.set_param('web.base.url.freeze', 'True')

env.company.name = name
site = env['website'].search([], limit=1)
site.write({'name': name, 'domain': base})

env.cr.commit()
print('TENANT_READY %s -> %s' % (name, base))
PY

echo
echo "Provisioned: http://$SLUG.localtest.me:$PORT"
