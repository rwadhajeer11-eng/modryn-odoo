# Railway demo image — single-tenant `te` on the free generated domain.
# This is the TEMPORARY public demo while modryn.co.il is stuck at the
# registrar; the real production design is deploy/ (VPS + nginx + systemd).
#
# Build context is the repo root (railway.toml sets dockerfilePath) and is
# deliberately TINY (~15 MB: addons + seed + these files). The 1.2 GB Odoo
# source is NOT uploaded — Railway's edge 413s large contexts — it is fetched
# here at the exact SHA the repo vendors (odoo/ is a clean checkout of
# odoo/odoo 19.0; verify with `git -C odoo status --short` before bumping).
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# psycopg2 and python-ldap ship no cp312 wheels — they need build-essential and
# the -dev headers. nodejs+rtlcss is NOT optional: the storefront is Hebrew/RTL
# and Odoo compiles RTL bundles through the external rtlcss binary; without it
# every page silently renders with LTR CSS.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential libpq-dev libldap2-dev libsasl2-dev \
      libpq5 libldap-2.5-0 libsasl2-2 libmagic1 \
      curl ca-certificates fonts-dejavu-core \
      postgresql-client nodejs npm util-linux \
    && npm install -g rtlcss \
    && rm -rf /var/lib/apt/lists/*

# Pin to the commit the repo vendors — this layer (and the pip layer under it)
# caches until the SHA changes.
ARG ODOO_COMMIT=6c8e7dcb96621dbbf634b0632f64a1be3cab7d94
RUN git init -q /app/odoo \
    && git -C /app/odoo remote add origin https://github.com/odoo/odoo.git \
    && git -C /app/odoo fetch -q --depth 1 origin "$ODOO_COMMIT" \
    && git -C /app/odoo checkout -q FETCH_HEAD \
    && rm -rf /app/odoo/.git

RUN pip install -r /app/odoo/requirements.txt

RUN useradd -m odoo

COPY addons /app/addons
# Seed filestore (~2 MB) and database dump (~7 MB) baked into the image: the
# entrypoint copies the filestore onto the volume on first boot, and
# bootstrap_restore.sh (run via railway ssh) restores the dump — the Postgres
# TCP proxy is not public, so the restore must run from inside the network.
# Refresh rule: re-dump and rebuild TOGETHER, or ir_attachment rows drift from
# the files on disk.
COPY deploy/railway/seed/filestore-te /bootstrap/filestore-te
# Plain SQL (gzipped), not -Fc: custom-format archives couple the container's
# pg_restore version to the laptop's pg_dump; plain SQL restores on any client.
COPY deploy/railway/seed/te.sql.gz /bootstrap/te.sql.gz
COPY deploy/railway/odoo.conf.railway deploy/railway/entrypoint.sh deploy/railway/bootstrap_restore.sh /app/deploy/railway/
RUN chmod +x /app/deploy/railway/entrypoint.sh /app/deploy/railway/bootstrap_restore.sh

WORKDIR /app
# Stays root here; the entrypoint chowns the Railway volume (mounted
# root-owned) and then drops to odoo via setpriv.
ENTRYPOINT ["/app/deploy/railway/entrypoint.sh"]
