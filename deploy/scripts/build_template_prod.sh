#!/usr/bin/env bash
# Build modryn_template on the production box: the golden database every
# boutique is cloned from. Run ONCE, as root, before the first tenant.
#
#   sudo /opt/modryn/deploy/scripts/build_template_prod.sh
#
# This is the production twin of scripts/build_template.sh. It differs in
# exactly one substantive way, and that difference is the reason it exists:
#
#   Odoo only ever creates a database from the database MANAGER, which
#   list_db = False switches off in production. `odoo-bin -d <name> -i <mods>`
#   does NOT create a missing database — it fails. So on this box the empty
#   database has to exist before Odoo is invoked at all, and it is created here
#   using EXACTLY the statement odoo/odoo/service/db.py issues — including
#   LC_COLLATE 'C', which that file notes "provides more useful indexes" and
#   which is only safe from template0.
#
#   It is created as the POSTGRES role for one reason only:
#   pg_stat_statements. Measured on PostgreSQL 16:
#     select name, trusted from pg_available_extension_versions
#      where name in ('pg_trgm','unaccent','pg_stat_statements');
#     pg_trgm t | unaccent t | pg_stat_statements f
#   pg_trgm and unaccent ARE trusted since PG 13/14 — the odoo role, which owns
#   the database, can create those itself. pg_stat_statements is not trusted and
#   needs a superuser. (An earlier version of this comment claimed pg_trgm was
#   untrusted and that Odoo would fail on it. Both halves were wrong: db.py
#   wraps that CREATE EXTENSION in a try/except that only logs a warning.)
#
# Every tenant is a `createdb -T` of this database, so the extensions, the
# modules and the unique indexes all come along for free and no tenant
# provisioning ever needs superuser.
#
# KEEP IN STEP with scripts/build_template.sh: the module list and the install
# ORDER below are load-bearing and are duplicated there.
set -euo pipefail

APP_ROOT=/opt/modryn
ODOO_CONF=/etc/odoo/odoo.conf
SERVICE_USER=odoo
TEMPLATE=modryn_template

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (needs the postgres role to create extensions)"

if sudo -u postgres psql -tAc "select 1 from pg_database where datname='$TEMPLATE'" | grep -q 1; then
  die "$TEMPLATE already exists. To rebuild: sudo -u postgres dropdb $TEMPLATE, then re-run."
fi

# ---------------------------------------------------------------------------
# 1. The empty database, byte-for-byte as Odoo would have made it
# ---------------------------------------------------------------------------
say "creating $TEMPLATE (postgres role: extensions need it, nothing else does)"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres <<SQL
CREATE DATABASE "$TEMPLATE" ENCODING 'unicode' LC_COLLATE 'C' TEMPLATE template0;
ALTER DATABASE "$TEMPLATE" OWNER TO "$SERVICE_USER";
SQL

# pg_trgm and unaccent are TRUSTED, so the owning role creates them itself. That
# is not pedantry: pg_dump emits `COMMENT ON EXTENSION` for every extension in
# the database, and COMMENT requires OWNERSHIP. Extensions created by postgres
# here are owned by postgres, so restore.sh — which runs pg_restore as the odoo
# role — takes an error on each one. Creating them as the odoo role makes those
# three errors two fewer, and the tenant clones inherit the ownership.
sudo -u "$SERVICE_USER" psql -v ON_ERROR_STOP=1 -d "$TEMPLATE" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
SQL

# pg_stat_statements is NOT trusted: superuser only, and there is no
# ALTER EXTENSION ... OWNER TO to hand it over afterwards. It stays owned by
# postgres, which is why restore.sh must tolerate exactly one pg_restore error.
#
# The load-test plan's server-side evidence depends on it. Created in the
# template so every tenant has it from birth rather than after someone
# remembers, mid-ramp, that stage 3's numbers have no query attribution.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$TEMPLATE" \
  -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements' >/dev/null

run_odoo() {
  # --db-filter overrides odoo.conf's ^%d$ so the template can be addressed by
  # name; -d overrides db_name, which deliberately does not list the template.
  #
  # --no-http is passed at every CALL SITE below, not here, because `odoo-bin
  # shell` and `odoo-bin server` take it in different positions relative to the
  # heredoc. It is NOT optional on either: with workers > 0, PreforkServer.run()
  # calls self.start() — which binds http_port — BEFORE preload_registries()
  # (odoo/odoo/service/server.py), so a --stop-after-init run on a box that is
  # already serving dies with
  #     OSError: [Errno 48] Address already in use
  # at socket.bind() having done nothing. Day-one provisioning survives only
  # because the runbook builds the template before starting Odoo; a REBUILD —
  # which new_boutique_prod.sh tells the operator to do mid-provisioning, on a
  # live box — hits it every time, and this script's refuse-to-run-twice guard
  # then leaves them hand-dropping a half-built template. Verified both ways on
  # the dev instance: without --no-http, EADDRINUSE; with it, registry loads and
  # the process exits cleanly while the server keeps serving.
  sudo -u "$SERVICE_USER" \
    PATH="$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin" TZ=UTC LANG=C.UTF-8 \
    "$APP_ROOT/.venv/bin/python" "$APP_ROOT/odoo/odoo-bin" "$@"
}

# ---------------------------------------------------------------------------
# 2. Base modules
# ---------------------------------------------------------------------------
say "installing base modules into $TEMPLATE (several minutes)"
# --without-demo keeps the template clean: demo products would clone into every
# boutique. In Odoo 19 this flag is a BOOL, not the old `all`.
run_odoo server -c "$ODOO_CONF" \
  -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" \
  -i website,website_sale,stock,calendar,portal,contacts \
  --load-language=he_IL \
  --without-demo=True \
  --no-http --stop-after-init

# ---------------------------------------------------------------------------
# 3. Locale, currency, variants
# ---------------------------------------------------------------------------
say "configuring template defaults (Hebrew, Arabic, ILS, variants)"
run_odoo shell -c "$ODOO_CONF" -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" --no-http <<'PY'
he = env['res.lang']._activate_lang('he_IL') or env['res.lang'].with_context(active_test=False).search([('code','=','he_IL')], limit=1)
ar = env['res.lang']._activate_lang('ar_001') or env['res.lang'].with_context(active_test=False).search([('code','=','ar_001')], limit=1)
(he | ar).write({'active': True})

ils = env['res.currency'].with_context(active_test=False).search([('name','=','ILS')], limit=1)
ils.write({'active': True})
env.company.currency_id = ils

# The size matrix (34..44) depends on product variants being enabled.
env['res.config.settings'].create({'group_product_variant': True}).execute()

site = env['website'].search([], limit=1)
site.write({'default_lang_id': he.id, 'language_ids': [(6, 0, [he.id, ar.id])]})

# odoo-bin shell does NOT commit on exit. Without this the whole configuration
# is written inside a transaction that is rolled back, and the success line
# below prints values that vanish when the shell closes.
env.cr.commit()
print('TEMPLATE_CONFIGURED langs=%s default=%s currency=%s' % (
    site.language_ids.mapped('code'), site.default_lang_id.code, env.company.currency_id.name))
PY

# ---------------------------------------------------------------------------
# 4. The MODRYN addons
# ---------------------------------------------------------------------------
# These go in the TEMPLATE, not per tenant: new_boutique_prod.sh is a
# `createdb -T` plus fixups, so whatever is not here is not in the boutique. A
# tenant cloned from a template without them 404s on /book, /floor and /my —
# and, the part that does not announce itself, carries NONE of the unique
# indexes, so it will happily sell one fitting room to two brides.
#
# AFTER the configuration step, not merged into the first -i: modryn_theme
# writes website pages and menus and should write them into a site that is
# already Hebrew-first with ILS.
say "installing modryn addons into $TEMPLATE"
run_odoo server -c "$ODOO_CONF" \
  -d "$TEMPLATE" --db-filter="^$TEMPLATE\$" \
  -i modryn_theme,modryn_booking,modryn_queue_poc,modryn_staff,modryn_portal,modryn_atelier,modryn_roster,modryn_ops \
  --without-demo=True \
  --no-http --stop-after-init

# ---------------------------------------------------------------------------
# 5. Prove the indexes exist
# ---------------------------------------------------------------------------
# modryn_portal's post_init_hook raises when a unique index did not get created,
# so reaching this line already suggests the template has them. Assert anyway:
# the hook only fires when the module ACTUALLY installs, and a re-run against an
# existing database is a no-op install that would skip it silently.
for idx in calendar_event_modryn_one_live_booking_per_slot \
           modryn_day_waitlist_modryn_one_offer_per_day; do
  if ! sudo -u postgres psql -d "$TEMPLATE" -tAc "select to_regclass('$idx')" | grep -q .; then
    die "$TEMPLATE is missing index $idx — every clone would inherit the hole"
  fi
done

# ---------------------------------------------------------------------------
# 6. Hand the template back to the odoo role
# ---------------------------------------------------------------------------
# Everything above ran as odoo except the CREATE DATABASE / CREATE EXTENSION,
# so ownership is already right; re-assert it so a future `createdb -T` by the
# odoo role cannot fail on a permission that drifted.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres \
  -c "ALTER DATABASE \"$TEMPLATE\" OWNER TO \"$SERVICE_USER\";" >/dev/null

echo
echo "Template ready. Next:"
echo "  sudo /opt/modryn/deploy/scripts/new_boutique_prod.sh bella 'Bella Bridal'"
