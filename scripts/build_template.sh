#!/usr/bin/env bash
# Build modryn_template: the golden database every boutique is cloned from.
# Anything configured here is inherited by every tenant, so keep it minimal.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
# shellcheck disable=SC1091
source .venv/bin/activate

TEMPLATE=modryn_template

if psql -d postgres -tAc "select 1 from pg_database where datname='$TEMPLATE'" | grep -q 1; then
  echo "!! $TEMPLATE already exists. Drop it first: dropdb $TEMPLATE"
  exit 1
fi

echo "==> initializing $TEMPLATE with base modules (takes a few minutes)"
# Odoo 19 splits the CLI into subcommands; `server` is the one that installs.
# --db-filter overrides odoo.conf's ^%d$ so we can address the DB by name.
# --without-demo keeps the template clean: demo products would clone into every
# boutique. (In 19 this flag is a BOOL, not the old `all`.)
./odoo/odoo-bin server -c odoo.conf \
  -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" \
  -i website,website_sale,stock,calendar,portal,contacts \
  --load-language=he_IL \
  --without-demo=True \
  --stop-after-init

echo "==> configuring template defaults (Hebrew, Arabic, ILS, variants)"
./odoo/odoo-bin shell -c odoo.conf -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" --no-http <<'PY'
# Hebrew active + default; Arabic installed as the second website language.
he = env['res.lang']._activate_lang('he_IL') or env['res.lang'].with_context(active_test=False).search([('code','=','he_IL')], limit=1)
ar = env['res.lang']._activate_lang('ar_001') or env['res.lang'].with_context(active_test=False).search([('code','=','ar_001')], limit=1)
(he | ar).write({'active': True})

# ILS as company currency. Israel prices in shekels; agorot precision matters.
ils = env['res.currency'].with_context(active_test=False).search([('name','=','ILS')], limit=1)
ils.write({'active': True})
env.company.currency_id = ils

# Product variants: the size matrix (34..44) depends on this being on.
env['res.config.settings'].create({'group_product_variant': True}).execute()

# Website: Hebrew default, Arabic available, language selector visible.
site = env['website'].search([], limit=1)
site.write({
    'default_lang_id': he.id,
    'language_ids': [(6, 0, [he.id, ar.id])],
})

env.cr.commit()
print('TEMPLATE_CONFIGURED langs=%s default=%s currency=%s' % (
    site.language_ids.mapped('code'), site.default_lang_id.code, env.company.currency_id.name))
PY

# The modryn addons go in the TEMPLATE, not per tenant. new_boutique.sh is a
# `createdb -T` plus fixups, so whatever is not here is not in the boutique: a
# tenant cloned from a template without these 404s on /book, /floor and /my,
# and — the part that does not announce itself — carries none of the unique
# indexes, so it will happily sell one fitting room to two brides.
# Installing once here also means a new tenant costs a clone (seconds) instead of
# a seven-module install (1-2 minutes), which is what the 30-tenant load-test
# budget in .planning/plans/load-test-plan.md §6 assumes.
#
# After the config step, not merged into the first -i: modryn_theme writes website
# pages and menus, and it should write them into a site that is already Hebrew-
# first with ILS. Same ordering the load-test plan's gold build uses.
echo "==> installing modryn addons into the template"
./odoo/odoo-bin server -c odoo.conf \
  -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" \
  -i modryn_theme,modryn_booking,modryn_queue_poc,modryn_staff,modryn_portal,modryn_atelier,modryn_roster,modryn_ops \
  --without-demo=True \
  --stop-after-init

# modryn_portal's post_init_hook raises when a unique index did not get created,
# so reaching this line already proves the template has them. Assert anyway: the
# hook only fires when the module actually installs, and a re-run of this script
# on an existing DB is a no-op install that would skip it silently.
for idx in calendar_event_modryn_one_live_booking_per_slot \
           modryn_day_waitlist_modryn_one_offer_per_day; do
  if ! psql -d "$TEMPLATE" -tAc "select to_regclass('$idx')" | grep -q .; then
    echo "!! $TEMPLATE is missing index $idx — every clone would inherit the hole"
    exit 1
  fi
done

echo
echo "Template ready. Next: ./scripts/new_boutique.sh bella 'Bella Bridal'"
