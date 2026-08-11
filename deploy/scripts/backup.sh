#!/usr/bin/env bash
# Nightly MODRYN backup. Driven by modryn-backup.timer at 02:30 UTC.
#
#   sudo /opt/modryn/deploy/scripts/backup.sh
#
# THE CONTRACT: non-zero exit on ANY failure, and never a silent partial. A
# backup script that half-works and exits 0 is worse than no backup at all,
# because it removes the anxiety that would otherwise have caught the gap.
# Every failure is recorded, the run continues so one bad tenant does not cost
# you the other nineteen, and the exit status at the end reflects all of them.
set -uo pipefail          # NOT -e: a failed tenant must be recorded, not fatal

APP_ROOT=/opt/modryn
DATA_DIR=/var/lib/modryn
ODOO_CONF=/etc/odoo/odoo.conf
ENV_FILE=/etc/modryn/deploy.env
SERVICE_USER=odoo
TEMPLATE=modryn_template
ROOT=/var/backups/modryn
STATUS=/var/log/modryn-backup.status
KEEP_DAILY=7
KEEP_WEEKLY=4
# Offsite keeps longer than local on purpose: the local tiers exist to make a
# same-day restore fast, the offsite tier exists to survive something that took
# the local tiers with it — including a deletion nobody noticed for a fortnight.
KEEP_OFFSITE=30

STAMP="$(date -u +%Y-%m-%d)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DEST="$ROOT/daily/$STAMP"
FAILURES=()

say()  { printf '==> %s\n' "$*"; }
fail() { printf '!! %s\n' "$*" >&2; FAILURES+=("$*"); }

trap 'finish' EXIT
finish() {
  local rc=0 line
  if [ "${#FAILURES[@]}" -gt 0 ]; then
    rc=1
    line="FAILED started=$STARTED finished=$(date -u +%Y-%m-%dT%H:%M:%SZ) errors=${#FAILURES[@]}: ${FAILURES[*]}"
  else
    line="OK started=$STARTED finished=$(date -u +%Y-%m-%dT%H:%M:%SZ) set=$DEST"
  fi
  # Single line, overwritten each run. Monitoring alerts on this file's AGE as
  # well as its contents: a backup that stopped running is silent by nature, and
  # "no news" from a cron job is indistinguishable from success.
  printf '%s\n' "$line" > "$STATUS"
  printf '%s\n' "$line"
  exit "$rc"
}

[ "$(id -u)" = 0 ] || { fail "must run as root"; exit 1; }
[ -r "$ODOO_CONF" ] || { fail "cannot read $ODOO_CONF"; exit 1; }
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

install -d -m 0700 "$DEST" || { fail "cannot create $DEST"; exit 1; }

# ---------------------------------------------------------------------------
# Tenant list — READ FROM odoo.conf, never a hard-coded array.
# ---------------------------------------------------------------------------
# A boutique provisioned on Tuesday must be in Tuesday night's backup without
# anyone remembering to edit a second file. modryn_template is appended
# EXPLICITLY because production deliberately removes it from db_name (a held
# connection blocks `createdb -T`) — losing it does not lose customer data, but
# it does lose the ability to provision, and rebuilding it is a multi-minute
# module install plus the Hebrew/Arabic/ILS configuration.
DB_LINE="$(grep -E '^[[:space:]]*db_name[[:space:]]*=' "$ODOO_CONF" | head -n 1)"
if [ -z "$DB_LINE" ]; then
  fail "no db_name line in $ODOO_CONF — refusing to guess which databases to dump"
  exit 1
fi
DBS="${DB_LINE#*=}"; DBS="${DBS//[[:space:]]/}"
IFS=',' read -r -a TENANTS <<< "$DBS"
TENANTS+=("$TEMPLATE")

# ---------------------------------------------------------------------------
# 1. Databases
# ---------------------------------------------------------------------------
# -Fc (custom format): restores selectively and in parallel, and a per-tenant
# file means one boutique can be restored without touching its neighbours —
# which is the main operational win of database-per-tenant and the reason a
# single pg_dumpall would be a downgrade.
for db in "${TENANTS[@]}"; do
  [ -n "$db" ] || continue
  say "pg_dump $db"
  if ! sudo -u "$SERVICE_USER" pg_dump -Fc --no-owner -f "$DEST/$db.dump" "$db" 2>>"$DEST/errors.log"; then
    fail "pg_dump failed for $db"
    # Do not leave a truncated file that looks like a backup.
    rm -f "$DEST/$db.dump"
  fi
done

# ---------------------------------------------------------------------------
# 2. Filestore
# ---------------------------------------------------------------------------
# Attachments are on DISK, not in Postgres. A database dump alone restores rows
# that point at files which do not exist — a tenant that looks intact and shows
# a broken image for every dress.
say "filestore"
if ! rsync -a --delete "$DATA_DIR/filestore/" "$DEST/filestore/" 2>>"$DEST/errors.log"; then
  fail "filestore rsync failed"
fi

# sessions/ is deliberately NOT backed up. Restoring week-old session files is
# worse than making everyone sign in again, and it is 4096 directories of churn.

# ---------------------------------------------------------------------------
# 3. Configuration
# ---------------------------------------------------------------------------
# Cheap, and it turns rebuild-from-bare-metal into a scripted operation instead
# of an archaeology exercise.
say "configuration"
install -d -m 0700 "$DEST/etc"
if ! rsync -a /etc/odoo /etc/nginx /etc/modryn \
        /etc/postgresql/16/main/conf.d \
        /etc/systemd/system/odoo.service \
        /etc/systemd/system/modryn-backup.service \
        /etc/systemd/system/modryn-backup.timer \
        "$DEST/etc/" 2>>"$DEST/errors.log"; then
  fail "config rsync failed"
fi
# These carry admin_passwd and the Twilio credentials.
chmod -R go-rwx "$DEST/etc"

# Record exactly what code produced this data set. Without it, a restore is a
# guess about which commit's schema the dump matches.
git -C "$APP_ROOT" rev-parse HEAD > "$DEST/COMMIT" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. Weekly tier
# ---------------------------------------------------------------------------
# Hardlinks, so four weeklies cost approximately nothing on top of the dailies
# they were promoted from. (Sunday = 7 with date's %u.)
if [ "$(date -u +%u)" = 7 ] && [ "${#FAILURES[@]}" -eq 0 ]; then
  say "promoting to weekly"
  install -d -m 0700 "$ROOT/weekly"
  rm -rf "${ROOT:?}/weekly/$STAMP"
  cp -al "$DEST" "$ROOT/weekly/$STAMP" || fail "weekly promotion failed"
fi

# ---------------------------------------------------------------------------
# 5. Retention
# ---------------------------------------------------------------------------
# Prune ONLY after a clean run. Deleting the last known-good set because
# tonight's failed is the exact shape of a two-disaster night.
prune() {
  local dir="$1" keep="$2" old
  [ -d "$dir" ] || return 0
  # shellcheck disable=SC2012  # names are ISO dates; ls sorts them correctly
  old="$(ls -1 "$dir" 2>/dev/null | sort -r | tail -n +"$((keep + 1))")"
  for d in $old; do
    say "pruning $dir/$d"
    rm -rf "${dir:?}/${d:?}"
  done
}
if [ "${#FAILURES[@]}" -eq 0 ]; then
  prune "$ROOT/daily"  "$KEEP_DAILY"
  prune "$ROOT/weekly" "$KEEP_WEEKLY"
else
  say "skipping retention prune: this run had failures"
fi

# ---------------------------------------------------------------------------
# 6. Offsite
# ---------------------------------------------------------------------------
# Seven local dailies on the same RAID1 pair survive a disk failure and NOT a
# filesystem, a fat-fingered dropdb, or the machine. Offsite is not optional,
# so its absence is reported every single night rather than assumed deliberate.
#
# ADDITIVE, NOT A MIRROR — this is the whole point of the section.
# The previous version was `rsync -a --delete "$ROOT/" "$OFFSITE_TARGET/"`,
# which made the remote a MIRROR of the local tree. A mirror protects against
# exactly one thing: this machine dying. It does not protect against the two
# failures people actually buy offsite storage for, because both propagate on
# the next nightly run:
#   - `rm -rf /var/backups/modryn` (or ransomware encrypting it) deletes the
#     offsite copy too, within 24 hours, with no prompt;
#   - a local retention bug that prunes too far prunes offsite identically.
# So: push ONLY tonight's dated set into its own remote directory, never with
# --delete, and let the REMOTE decide what to expire, by age, on its own. A
# local wipe now costs zero offsite sets.
#
# --link-dest hardlinks anything byte-identical to last night's remote set, so
# 30 nights of a mostly-static filestore cost roughly one filestore. If last
# night's set is absent (a missed night, or the first run) rsync warns and
# copies in full — space, not correctness.
#
# Only on a clean run, for the same reason the local prune is: pushing a set
# that is missing a tenant, and then letting remote retention age out the last
# good one behind it, is how a bad night becomes a bad quarter.
if [ -z "${OFFSITE_TARGET:-}" ]; then
  fail "OFFSITE_TARGET unset — backups are LOCAL ONLY and do not survive this machine"
elif [ "${#FAILURES[@]}" -ne 0 ]; then
  say "skipping offsite push: this run had failures"
else
  say "offsite -> $OFFSITE_TARGET/daily/$STAMP"
  SSH="ssh -i ${OFFSITE_SSH_KEY:-/root/.ssh/modryn_backup} -o StrictHostKeyChecking=yes -o BatchMode=yes"
  PREV_STAMP="$(date -u -d yesterday +%Y-%m-%d)"
  # rsync creates the leaf directory but not its parents; --mkpath is rsync 3.2.3+
  # (Ubuntu 24.04 ships 3.2.7) and saves a separate ssh mkdir round trip.
  if ! rsync -a --mkpath --link-dest="../$PREV_STAMP" \
        -e "$SSH" \
        "$DEST/" "$OFFSITE_TARGET/daily/$STAMP/" 2>>"$DEST/errors.log"; then
    fail "offsite rsync to $OFFSITE_TARGET failed"
  fi

  # Remote retention, computed REMOTELY from what is actually there. Nothing
  # about this depends on the local tree, which is what keeps a local wipe from
  # reaching offsite. Names are ISO dates, so a reverse sort is newest-first.
  OFFSITE_HOST="${OFFSITE_TARGET%%:*}"
  OFFSITE_PATH="${OFFSITE_TARGET#*:}"
  # shellcheck disable=SC2086  # $SSH must word-split into ssh + its flags
  if [ "$OFFSITE_HOST" = "$OFFSITE_TARGET" ]; then
    fail "OFFSITE_TARGET '$OFFSITE_TARGET' has no host: prefix — cannot apply remote retention"
  elif ! $SSH "$OFFSITE_HOST" \
        "cd '$OFFSITE_PATH/daily' && ls -1 | sort -r | tail -n +$((KEEP_OFFSITE + 1)) | xargs -r rm -rf" \
        2>>"$DEST/errors.log"; then
    fail "offsite retention prune on $OFFSITE_HOST failed — it will fill up"
  fi
fi

# errors.log is left in place when non-empty; remove it when the run was clean
# so its presence always means something.
[ -s "$DEST/errors.log" ] || rm -f "$DEST/errors.log"
