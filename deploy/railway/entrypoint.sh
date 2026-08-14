#!/bin/sh
# Railway container entrypoint. Fails loudly on any missing variable (set -u +
# ${VAR:?}) — a demo that boots with a half-configured database is worse than
# one that crashes with the variable's name in the log.
set -eu

CONF=/tmp/odoo.conf
# sed delimiter is | — Railway passwords/hashes are base64-ish ($ . / +) and
# contain no | & or \. If Railway ever generates one that does, substitution
# breaks loudly here, not silently downstream.
sed -e "s|@PGHOST@|${PGHOST:?}|" \
    -e "s|@PGPORT@|${PGPORT:-5432}|" \
    -e "s|@PGUSER@|${PGUSER:?}|" \
    -e "s|@PGPASSWORD@|${PGPASSWORD:?}|" \
    -e "s|@PORT@|${PORT:-8069}|" \
    -e "s|@ADMIN_PASSWD@|${ODOO_ADMIN_PASSWD_HASH:?}|" \
    /app/deploy/railway/odoo.conf.railway > "$CONF"

mkdir -p /data/filestore /data/sessions
# First boot only: seed the tenant filestore baked into the image.
[ -d /data/filestore/te ] || cp -R /bootstrap/filestore-te /data/filestore/te
# Railway mounts the volume root-owned; odoo must own it to write attachments
# and sessions.
chown -R odoo:odoo /data

exec setpriv --reuid odoo --regid odoo --init-groups \
    python3 /app/odoo/odoo-bin server -c "$CONF"
