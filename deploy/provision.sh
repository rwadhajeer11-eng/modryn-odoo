#!/usr/bin/env bash
# Provision a bare Ubuntu 24.04 LTS box into a MODRYN production host.
#
#   sudo /opt/modryn/deploy/provision.sh
#
# IDEMPOTENT. Every step is guarded and re-running is the normal way to apply a
# change from the repo. That is not a nicety: this script IS the reproducibility
# argument that justified not using Docker. If it stops being re-runnable, the
# no-container decision stops being defensible.
#
# It NEVER contains a secret. On the first run it writes /etc/modryn/deploy.env
# with 0600 permissions and placeholder values, then stops and tells you to fill
# it in. Secrets live there and in /etc/odoo/odoo.conf (0640 root:odoo); neither
# is in the repository.
set -euo pipefail

# --- Constants -------------------------------------------------------------
SERVICE_USER=odoo
APP_ROOT=/opt/modryn                 # the repository checkout
DATA_DIR=/var/lib/modryn             # filestore/ + sessions/ — the live state
ENV_FILE=/etc/modryn/deploy.env
ODOO_CONF=/etc/odoo/odoo.conf
PG_VERSION=16
STATIC_ROOT=/var/www/modryn-static
ACME_ROOT=/var/www/acme
BACKUP_ROOT=/var/backups/modryn
ODOO_BRANCH=19.0

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
skip() { printf '    \033[2m--\033[0m %s\n' "$*"; }
die()  { printf '\033[31m!!\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo $0)"

# Ubuntu 24.04 is not decoration: it is where python3.12 and postgresql-16 are
# both in the default archive. On 22.04 both need a PPA and the pinned versions
# below silently do not exist.
if ! grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
  die "expected Ubuntu 24.04 LTS; got $(. /etc/os-release && echo "$PRETTY_NAME")"
fi

# ---------------------------------------------------------------------------
# 1. Operator-owned settings
# ---------------------------------------------------------------------------
# Created empty-but-shaped on the first run so nothing here has to be typed
# from memory at 3am, and so no value in it is ever in git.
if [ ! -f "$ENV_FILE" ]; then
  say "creating $ENV_FILE — FILL IT IN AND RE-RUN"
  install -d -m 0750 -o root -g root /etc/modryn
  cat > "$ENV_FILE" <<'ENVEOF'
# MODRYN deployment settings. 0600 root:root. NEVER commit this file.
# Re-run provision.sh after editing.

# The apex domain tenants live under: bella.<DOMAIN>, noga.<DOMAIN>, ...
# Substituted into the nginx server_name regexes.
DOMAIN=

# Where to clone the application repository from, if /opt/modryn is empty.
# Leave blank if you place the checkout at /opt/modryn yourself.
REPO_URL=
REPO_REF=main

# pbkdf2_sha512 hash of the Odoo master password — NEVER the plaintext.
# Generate it (after the venv exists) with:
#   /opt/modryn/.venv/bin/python - <<'PY'
#   from passlib.context import CryptContext; import getpass
#   print(CryptContext(schemes=['pbkdf2_sha512'],
#         pbkdf2_sha512__rounds=600_000).hash(getpass.getpass('master: ')))
#   PY
# Store the plaintext in a password manager. If this is left blank the first
# run installs a placeholder that refuses every authentication, which is safe.
ADMIN_PASSWD_HASH=

# Offsite backup target for rsync-over-ssh. MUST be user@host:/path — backup.sh
# splits on the colon to run its remote retention prune over ssh.
# e.g. u12345@u12345.your-storagebox.de:/modryn
#
# Each clean run pushes only that night's set to <path>/daily/<date>/ and keeps
# the newest 30 there, pruned by listing the REMOTE directory. It is additive:
# nothing local, including `rm -rf /var/backups/modryn`, deletes anything there.
# Budget for 30 dated sets; unchanged files are hardlinked to the previous night.
#
# Leave blank to keep backups local ONLY — backup.sh will say so, loudly, every
# night, because seven local dailies on one RAID1 pair do not survive a dropdb.
OFFSITE_TARGET=
OFFSITE_SSH_KEY=/root/.ssh/modryn_backup

# The ramp generator's source address, for the nginx rate-limit exemption.
# WITHOUT THIS the load campaign measures nginx 429s and calls the result an
# Odoo capacity ceiling. REMOVE IT AGAIN when the campaign is over.
LOADGEN_IP=

# Twilio, per platform. Loaded into each tenant's ir.config_parameter by
# new_boutique_prod.sh. Missing credentials are SAFE: modryn.sms.send() logs the
# message and returns success rather than raising, so an unconfigured tenant
# silently texts nobody instead of 500ing. Know that before reading "logged" as
# "delivered".
TWILIO_ACCOUNT_SID=
TWILIO_API_KEY_SID=
TWILIO_API_KEY_SECRET=
TWILIO_FROM_NUMBER=
ENVEOF
  chmod 0600 "$ENV_FILE"
  die "edit $ENV_FILE (at minimum DOMAIN) and run this script again"
fi

chmod 0600 "$ENV_FILE"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

[ -n "${DOMAIN:-}" ] || die "DOMAIN is empty in $ENV_FILE"

# ---------------------------------------------------------------------------
# 2. System packages
# ---------------------------------------------------------------------------
say "system packages"
export DEBIAN_FRONTEND=noninteractive

PKGS=(
  # Odoo runtime
  python3.12 python3.12-venv python3.12-dev build-essential
  # psycopg2 and lxml build from source when no wheel matches
  libpq-dev libxml2-dev libxslt1-dev libldap2-dev libsasl2-dev libffi-dev
  # Pillow / reportlab image backends
  libjpeg-dev zlib1g-dev libtiff-dev libopenjp2-7-dev libfreetype-dev
  # rlPyCairo is NOT in odoo/requirements.txt but IS required to render the
  # /queue/sign QR code. It needs pkg-config + cairo headers AT BUILD TIME, and
  # a box missing them fails at pip install, not at runtime.
  pkg-config libcairo2-dev
  # wkhtmltopdf substitute for PDF reports
  fonts-liberation
  postgresql-"$PG_VERSION" postgresql-client-"$PG_VERSION"
  nginx
  certbot
  nodejs npm
  ufw fail2ban unattended-upgrades
  git curl rsync openssl ca-certificates acl
)
apt-get update -qq
apt-get install -y -qq "${PKGS[@]}"

# ---------------------------------------------------------------------------
# 3. Service user and directories
# ---------------------------------------------------------------------------
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  skip "user $SERVICE_USER exists"
else
  say "creating service user $SERVICE_USER"
  # No shell: Odoo writes attachments whose content an anonymous visitor can
  # influence. If that process is ever escaped from, it should not also be a
  # login. No home either — data_dir is the only place it writes.
  adduser --system --group --home "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

say "directories"
# data_dir is owned by the service user; APP_ROOT is NOT — the process must not
# be able to rewrite its own code.
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR/filestore"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR/sessions"
# nginx serves filestore bytes on Odoo's behalf via X-Accel-Redirect, so the
# www-data user must be able to traverse into it. An ACL rather than a mode
# change, so the directory does not become group- or world-readable wholesale.
setfacl -R  -m u:www-data:rX "$DATA_DIR/filestore"
setfacl -Rd -m u:www-data:rX "$DATA_DIR/filestore"
setfacl     -m u:www-data:x  "$DATA_DIR"

install -d -m 0750 -o root -g "$SERVICE_USER" /etc/odoo
install -d -m 0755 -o root -g root "$STATIC_ROOT"
install -d -m 0755 -o root -g root "$ACME_ROOT"
install -d -m 0700 -o root -g root "$BACKUP_ROOT"
install -d -m 0755 -o root -g root /etc/nginx/snippets
install -d -m 0755 -o root -g root /etc/nginx/ssl

# ---------------------------------------------------------------------------
# 4. Application checkout
# ---------------------------------------------------------------------------
if [ -d "$APP_ROOT/.git" ]; then
  skip "application checkout present at $APP_ROOT"
elif [ -n "${REPO_URL:-}" ]; then
  say "cloning application from $REPO_URL"
  git clone --branch "${REPO_REF:-main}" "$REPO_URL" "$APP_ROOT"
else
  die "no checkout at $APP_ROOT and REPO_URL is empty in $ENV_FILE"
fi
git config --global --add safe.directory "$APP_ROOT" || true

# Odoo core: shallow clone, pinned to the branch, NEVER edited. Every custom
# behaviour reaches core through model/view inheritance, which is the only
# reason a version bump is survivable at all.
if [ -d "$APP_ROOT/odoo/.git" ]; then
  skip "odoo core present"
else
  say "cloning Odoo $ODOO_BRANCH (shallow)"
  git clone --depth 1 --branch "$ODOO_BRANCH" https://github.com/odoo/odoo.git "$APP_ROOT/odoo"
fi

# ---------------------------------------------------------------------------
# 5. Python environment
# ---------------------------------------------------------------------------
if [ -x "$APP_ROOT/.venv/bin/python" ]; then
  skip "venv present"
else
  # Odoo 19 supports 3.10-3.12. 3.13 is NOT supported by every dependency, and
  # the failure is a build error deep in a wheel rather than a clear message.
  say "creating venv (python3.12)"
  python3.12 -m venv "$APP_ROOT/.venv"
fi

say "python requirements"
"$APP_ROOT/.venv/bin/pip" install -q -U pip wheel setuptools
"$APP_ROOT/.venv/bin/pip" install -q -r "$APP_ROOT/odoo/requirements.txt"
# Not in requirements.txt, and its absence fails SILENTLY at /queue/sign.
"$APP_ROOT/.venv/bin/pip" install -q rlPyCairo
# passlib is used to generate the admin_passwd hash in the runbook.
"$APP_ROOT/.venv/bin/pip" install -q passlib

# ---------------------------------------------------------------------------
# 6. RTL toolchain
# ---------------------------------------------------------------------------
# Odoo builds its RTL stylesheet by shelling out to `rtlcss` over the compiled
# LTR CSS. Without it on PATH a Hebrew storefront renders LTR-ish and NOTHING
# logs an error — which is why this is a provisioning step and not a footnote.
if command -v rtlcss >/dev/null 2>&1; then
  skip "rtlcss present"
else
  say "installing rtlcss"
  npm install -g --silent rtlcss
fi

# ---------------------------------------------------------------------------
# 7. PostgreSQL
# ---------------------------------------------------------------------------
say "postgresql $PG_VERSION"
PG_CONF_D="/etc/postgresql/$PG_VERSION/main/conf.d"
install -d -m 0755 "$PG_CONF_D"

PG_NEEDS_RESTART=0
if ! cmp -s "$DEPLOY_DIR/postgresql/tuning.conf" "$PG_CONF_D/modryn.conf"; then
  install -m 0644 -o root -g root "$DEPLOY_DIR/postgresql/tuning.conf" "$PG_CONF_D/modryn.conf"
  # shared_preload_libraries, shared_buffers, max_connections and
  # listen_addresses are all restart-only. A reload here would leave
  # pg_stat_statements unloaded while the file claims otherwise.
  PG_NEEDS_RESTART=1
fi
systemctl enable --now postgresql >/dev/null
if [ "$PG_NEEDS_RESTART" = 1 ]; then
  say "restarting postgresql (restart-only settings changed)"
  systemctl restart postgresql
fi

# The odoo role: CREATEDB but NOT superuser. CREATEDB is required because
# tenants are provisioned with `createdb -T modryn_template`. Superuser is not,
# because a template clone copies the extensions with it — only the ONE-TIME
# template build needs CREATE EXTENSION, and build_template_prod.sh does that
# part as the postgres role. Peer auth over the unix socket is the credential;
# there is no password to leak because there is no TCP listener.
if sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='$SERVICE_USER'" | grep -q 1; then
  skip "postgres role $SERVICE_USER exists"
else
  say "creating postgres role $SERVICE_USER"
  sudo -u postgres createuser --createdb --no-createrole --no-superuser "$SERVICE_USER"
fi

# ---------------------------------------------------------------------------
# 8. Odoo configuration
# ---------------------------------------------------------------------------
say "odoo.conf"
# An EMPTY admin_passwd hash makes verify_admin_password return False for every
# input, which is stricter than any password. That is the safe default while the
# operator has not generated a real hash yet.
HASH="${ADMIN_PASSWD_HASH:-}"
if [ -z "$HASH" ]; then
  HASH=""
  printf '    \033[33m!!\033[0m ADMIN_PASSWD_HASH is empty — the master password will refuse every login (safe, but recovery via the db manager is impossible until you set it)\n'
fi

if [ -f "$ODOO_CONF" ]; then
  # NEVER clobber a live config: db_name is edited in place by
  # new_boutique_prod.sh, so overwriting this file un-provisions every boutique
  # added since the last provision run.
  skip "$ODOO_CONF exists — left alone (see deploy/README.md to apply changes)"
else
  sed "s|__ADMIN_PASSWD_HASH__|${HASH}|" "$DEPLOY_DIR/odoo.conf.prod" > "$ODOO_CONF"
  chown root:"$SERVICE_USER" "$ODOO_CONF"
  chmod 0640 "$ODOO_CONF"
fi

# ---------------------------------------------------------------------------
# 9. systemd
# ---------------------------------------------------------------------------
say "systemd units"
UNITS_CHANGED=0
for unit in odoo.service modryn-backup.service modryn-backup.timer; do
  if ! cmp -s "$DEPLOY_DIR/systemd/$unit" "/etc/systemd/system/$unit"; then
    install -m 0644 -o root -g root "$DEPLOY_DIR/systemd/$unit" "/etc/systemd/system/$unit"
    UNITS_CHANGED=1
  fi
done
[ "$UNITS_CHANGED" = 1 ] && systemctl daemon-reload
systemctl enable odoo.service >/dev/null
systemctl enable --now modryn-backup.timer >/dev/null

# ---------------------------------------------------------------------------
# 10. nginx
# ---------------------------------------------------------------------------
say "nginx"
install -m 0644 -o root -g root "$DEPLOY_DIR/nginx/www/404.html"    "$STATIC_ROOT/404.html"
install -m 0644 -o root -g root "$DEPLOY_DIR/nginx/www/50x.html"    "$STATIC_ROOT/50x.html"
install -m 0644 -o root -g root "$DEPLOY_DIR/nginx/www/robots.txt"  "$STATIC_ROOT/robots.txt"

# TLS certificate paths live in a swappable snippet because nginx cannot take a
# variable in ssl_certificate, and the box has to be able to start BEFORE
# certbot has ever run (DNS-01 needs an API token this script does not have).
LE_LIVE="/etc/letsencrypt/live/$DOMAIN"
if [ -f "$LE_LIVE/fullchain.pem" ]; then
  say "using Let's Encrypt certificate for $DOMAIN"
  cat > /etc/nginx/snippets/modryn-tls.conf <<EOF
# Managed by provision.sh — repointed automatically once certbot has issued.
ssl_certificate     $LE_LIVE/fullchain.pem;
ssl_certificate_key $LE_LIVE/privkey.pem;
ssl_trusted_certificate $LE_LIVE/chain.pem;
EOF
else
  if [ ! -f /etc/nginx/ssl/modryn-placeholder.crt ]; then
    say "generating a self-signed PLACEHOLDER certificate (certbot has not run)"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN,DNS:*.$DOMAIN" \
      -keyout /etc/nginx/ssl/modryn-placeholder.key \
      -out    /etc/nginx/ssl/modryn-placeholder.crt >/dev/null 2>&1
    chmod 0600 /etc/nginx/ssl/modryn-placeholder.key
  fi
  cat > /etc/nginx/snippets/modryn-tls.conf <<'EOF'
# PLACEHOLDER self-signed certificate. nginx refuses to start a `listen 443 ssl`
# block without a certificate, and DNS-01 issuance needs an operator-owned API
# token — so the box boots on this and provision.sh repoints the snippet at
# Let's Encrypt the next time it runs. Every browser will warn until then.
ssl_certificate     /etc/nginx/ssl/modryn-placeholder.crt;
ssl_certificate_key /etc/nginx/ssl/modryn-placeholder.key;
EOF
  printf '    \033[33m!!\033[0m TLS is a SELF-SIGNED PLACEHOLDER. Issue the real wildcard (see deploy/README.md) and re-run this script.\n'
fi

# The rate-limit exemption include is always present so `geo` can include it
# unconditionally; it is empty unless LOADGEN_IP is set.
if [ -n "${LOADGEN_IP:-}" ]; then
  printf '%s/32 1;\n' "$LOADGEN_IP" > /etc/nginx/modryn-loadgen-exempt.conf
  printf '    \033[33m!!\033[0m rate-limit exemption ACTIVE for %s — clear LOADGEN_IP and re-run when the ramp is over\n' "$LOADGEN_IP"
else
  : > /etc/nginx/modryn-loadgen-exempt.conf
fi
chmod 0644 /etc/nginx/modryn-loadgen-exempt.conf

sed "s/__DOMAIN__/$DOMAIN/g" "$DEPLOY_DIR/nginx/modryn-http.conf" > /etc/nginx/conf.d/modryn-http.conf
sed "s/__DOMAIN__/$DOMAIN/g" "$DEPLOY_DIR/nginx/modryn-site.conf" > /etc/nginx/sites-available/modryn.conf
ln -sfn /etc/nginx/sites-available/modryn.conf /etc/nginx/sites-enabled/modryn.conf

# Ubuntu's stock site is also a default_server. Two default_servers on the same
# address is a hard `nginx -t` failure, and if ours lost the race an unknown
# subdomain would reach Odoo's database selector.
if [ -e /etc/nginx/sites-enabled/default ]; then
  say "removing the distribution default site (conflicting default_server)"
  rm -f /etc/nginx/sites-enabled/default
fi

# tenants.map is DERIVED from odoo.conf's db_name; never hand-written.
"$DEPLOY_DIR/scripts/gen_tenants_map.sh"

nginx -t
systemctl enable --now nginx >/dev/null
systemctl reload nginx

# ---------------------------------------------------------------------------
# 11. Firewall
# ---------------------------------------------------------------------------
say "ufw"
# Odoo binds 127.0.0.1 (http_interface in odoo.conf), so 8069/8072 must never
# be reachable. If a future edit ever loosens that bind, this deny-by-default is
# the thing standing between the internet and /web/database/.
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed 's/^/    /'

# ---------------------------------------------------------------------------
# 12. fail2ban
# ---------------------------------------------------------------------------
say "fail2ban"
install -m 0644 -o root -g root "$DEPLOY_DIR/fail2ban/modryn-odoo.conf"  /etc/fail2ban/filter.d/modryn-odoo.conf
install -m 0644 -o root -g root "$DEPLOY_DIR/fail2ban/jail-modryn.conf"  /etc/fail2ban/jail.d/modryn.conf
systemctl enable --now fail2ban >/dev/null
systemctl reload fail2ban 2>/dev/null || systemctl restart fail2ban

# ---------------------------------------------------------------------------
# 13. Unattended security upgrades
# ---------------------------------------------------------------------------
say "unattended-upgrades"
cat > /etc/apt/apt.conf.d/52modryn-unattended <<'EOF'
// Security updates only, applied automatically.
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
// AUTOMATIC REBOOT IS OFF ON PURPOSE. An unplanned reboot during boutique
// hours is worse than a kernel patch landing an hour late. `needrestart` and
// /var/run/reboot-required tell you when one is due; schedule it.
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
EOF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# ---------------------------------------------------------------------------
# 14. Report
# ---------------------------------------------------------------------------
say "done"
cat <<EOF

  Provisioned. What is NOT done yet, and cannot be done from here:

    1. TLS. Issue the wildcard over DNS-01 (needs your DNS API token):
         certbot certonly --dns-<provider> \\
           --dns-<provider>-credentials /etc/letsencrypt/dns.ini \\
           -d $DOMAIN -d '*.$DOMAIN' \\
           --deploy-hook 'systemctl reload nginx'
       then re-run this script so the TLS snippet repoints at it.

    2. The golden template — nothing can be provisioned without it:
         sudo $DEPLOY_DIR/scripts/build_template_prod.sh

    3. The first boutique:
         sudo $DEPLOY_DIR/scripts/new_boutique_prod.sh bella "Bella Bridal"

    4. Start serving:
         systemctl start odoo && systemctl status odoo

  Read $DEPLOY_DIR/README.md before step 2.
EOF
