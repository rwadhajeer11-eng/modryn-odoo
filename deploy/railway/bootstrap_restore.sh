#!/bin/sh
# Restore the demo tenant `te` into Railway Postgres. Runs INSIDE the deployed
# container (the Postgres TCP proxy is not exposed publicly, but the container
# reaches postgres.railway.internal and the image ships postgresql-client plus
# the seed dump at /bootstrap/te.dump). Modeled on deploy/scripts/restore.sh:
# same collation, same error classification, same fixup set.
#
# From the laptop:
#   railway ssh -- /app/deploy/railway/bootstrap_restore.sh https://<host> [force]
#
# Then restart the service (fresh registry): railway redeploy --yes
#
# Consistency rule: the dump and the filestore are baked into the SAME image
# (deploy/railway/Dockerfile) — refresh them together or ir_attachment rows
# drift from the files on the volume.
set -eu

BASE_URL="${1:?usage: bootstrap_restore.sh https://<host> [force]}"
DUMP=/bootstrap/te.sql.gz
LOG=/tmp/te_restore.log
# psql/pg_restore read PGHOST/PGPORT/PGUSER/PGPASSWORD from the service env.
MAINT=railway

echo "==> checking for an existing te database"
EXISTS=$(psql -d "$MAINT" -tAc "select 1 from pg_database where datname='te'" || echo "")
if [ "$EXISTS" = "1" ]; then
  if [ "${2:-}" = "force" ]; then
    echo "==> force — dropping te"
    psql -d "$MAINT" -v ON_ERROR_STOP=1 -c "DROP DATABASE te WITH (FORCE)"
  else
    echo "!! database te already exists — pass 'force' as the second argument to replace it"
    exit 1
  fi
fi

# Same collation contract as deploy/scripts/restore.sh: Odoo assumes C collation.
echo "==> creating te"
psql -d "$MAINT" -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE te ENCODING 'unicode' LC_COLLATE 'C' TEMPLATE template0"

echo "==> restoring $DUMP"
# Plain SQL through psql (version-agnostic — a -Fc archive from a newer
# laptop pg_dump is unreadable to the container's older pg_restore). The odoo
# role creates pg_trgm itself (trusted extension, it is the db owner), so the
# only tolerable error class is COMMENT ON EXTENSION ownership — the same
# classification deploy/scripts/restore.sh uses. Anything else is real.
gunzip -c "$DUMP" | psql -d te -v ON_ERROR_STOP=0 -q >/dev/null 2>"$LOG" || true
REAL=$(grep -c '^ERROR:' "$LOG" || true)
BENIGN=$(grep -cE '^ERROR:.*(comment on extension|must be owner of extension)' "$LOG" || true)
if [ "$REAL" -gt "$BENIGN" ]; then
  echo "!! restore produced $REAL errors ($BENIGN benign extension-comment):"
  grep '^ERROR:' "$LOG" | grep -viE 'comment on extension|must be owner of extension' | head -10
  exit 1
fi
echo "    restored ($REAL errors, all benign)"

echo "==> applying per-deployment fixups"
# Mirrors the restore.sh fixup set in plain SQL (each is an ICP row or
# website.domain). database.secret is NOT rotated: new_boutique.sh already
# regenerated it at clone time, so te's secret is unique to te.
psql -d te -v ON_ERROR_STOP=1 <<SQL
INSERT INTO ir_config_parameter(key, value)
VALUES ('web.base.url', '$BASE_URL'), ('web.base.url.freeze', 'True')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
UPDATE ir_config_parameter SET value = gen_random_uuid()::text WHERE key = 'database.uuid';
-- Strip any per-tenant credential copy the dump carried, but NEVER
-- modryn.twilio.disabled: that key matches the same glob and is the off switch,
-- so deleting it would hand a restored demo the platform sender from the process
-- environment. Since the credentials moved out of the database, this DELETE no
-- longer makes te unable to send — what keeps the public demo quiet is that the
-- Railway service exports no TWILIO_* variables. Set them there and it sends.
DELETE FROM ir_config_parameter
 WHERE key LIKE 'modryn.twilio.%' AND key <> 'modryn.twilio.disabled';
UPDATE website SET domain = '$BASE_URL';
SQL

echo "DONE — restart the service so the registry loads fresh:  railway redeploy --yes"
