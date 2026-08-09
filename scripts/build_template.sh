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

echo
echo "Template ready. Next: ./scripts/new_boutique.sh bella 'Bella Bridal'"
