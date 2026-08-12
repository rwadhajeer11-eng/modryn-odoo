# MODRYN production runbook

One Ubuntu 24.04 box. Odoo 19 Community under systemd, self-hosted PostgreSQL 16 reachable
only over the unix socket, nginx in front. No containers, no orchestrator.

This file is written to be followed at 3am by someone who did not build it. Commands are
literal. Where a step needs a judgement call, it says so and stops.

Full reasoning behind every number and every directive lives in
`.planning/specs/deployment-spec.md`. Corrections found while building these artifacts are at
the bottom of this file under **Where the spec was wrong**.

---

## 0. The map

| Path | What |
|---|---|
| `/opt/modryn` | the application checkout (this repo). Owned by root — the service user must not be able to rewrite its own code |
| `/opt/modryn/odoo` | Odoo 19 shallow clone. **Never edited** |
| `/opt/modryn/.venv` | Python 3.12 environment |
| `/var/lib/modryn` | `data_dir` — `filestore/` and `sessions/`. **This is the live state.** Not in the repo |
| `/etc/odoo/odoo.conf` | production config, `0640 root:odoo` (holds `admin_passwd`) |
| `/etc/modryn/deploy.env` | operator secrets, `0600 root:root`. **Never in git** |
| `/etc/nginx/conf.d/modryn-http.conf` | upstreams, maps, rate-limit zones, log format |
| `/etc/nginx/sites-enabled/modryn.conf` | the catch-all and the tenant server block |
| `/etc/nginx/tenants.map` | **generated** from `db_name`. Do not hand-edit |
| `/var/backups/modryn` | `daily/` (7) and `weekly/` (4) |
| `/var/log/modryn-backup.status` | one line. Monitor its **age** as well as its contents |

Services: `odoo.service`, `nginx`, `postgresql`, `modryn-backup.timer`, `fail2ban`.

---

## 1. Provision a new box

```bash
# as root on a fresh Ubuntu 24.04
apt-get update && apt-get install -y git
git clone <REPO_URL> /opt/modryn
/opt/modryn/deploy/provision.sh
```

The first run writes `/etc/modryn/deploy.env` and **stops**. Fill it in — at minimum `DOMAIN`
— then run `provision.sh` again. It is idempotent; re-running is the normal way to apply a
change from the repo.

Then, in order:

**a. TLS.** Wildcard over **DNS-01** — HTTP-01 cannot issue a wildcard, and per-tenant
certificates would put a third-party rate limit inside the tenant-provisioning path.

```bash
certbot certonly --dns-<provider> \
  --dns-<provider>-credentials /etc/letsencrypt/dns.ini \
  -d "$DOMAIN" -d "*.$DOMAIN" \
  --deploy-hook 'systemctl reload nginx'
chmod 0600 /etc/letsencrypt/dns.ini
/opt/modryn/deploy/provision.sh          # repoints the TLS snippet at Let's Encrypt
```

Until this runs, nginx serves a **self-signed placeholder** so the box can boot at all. Every
browser will warn. That is expected, and `provision.sh` says so.

**b. The master password hash.**

```bash
/opt/modryn/.venv/bin/python - <<'PY'
from passlib.context import CryptContext
import getpass
print(CryptContext(schemes=['pbkdf2_sha512'], pbkdf2_sha512__rounds=600_000)
      .hash(getpass.getpass('master password: ')))
PY
```

Put the **hash** in `ADMIN_PASSWD_HASH` in `/etc/modryn/deploy.env`, the plaintext in a
password manager, and the hash into `admin_passwd` in `/etc/odoo/odoo.conf`. (`provision.sh`
refuses to overwrite an existing `odoo.conf`, because `db_name` in it is edited in place by
tenant provisioning.)

**c. The golden template.** Nothing can be provisioned without it. ~5 minutes.

```bash
/opt/modryn/deploy/scripts/build_template_prod.sh
```

**d. The first boutique**, then start serving:

```bash
/opt/modryn/deploy/scripts/new_boutique_prod.sh bella "Bella Bridal"
systemctl start odoo
```

**e. Point the regression suite at this box.** `scripts/verify.sh` takes `BASE_HOST` and
`BASE_SCHEME`, so it addresses the real hostnames **through nginx**:

```bash
sudo -u odoo env BASE_HOST="$DOMAIN" BASE_SCHEME=https ODOO_CONF=/etc/odoo/odoo.conf \
  MODRYN_DEMO_PASSWORD='<seeded password>' /opt/modryn/scripts/verify.sh
```

`deploy.sh` passes all four for you; this is the form for running it by hand.

Three things that are not optional:

- **`ODOO_CONF` must be explicit.** `/opt/modryn` is a checkout of the repository, so
  `./odoo.conf` exists here too — and it is the *developer's*, listing a laptop's tenants.
- **As `odoo`, not root.** The suite makes 72 `psql -d <tenant>` calls authenticating by peer,
  and there is no `root` PostgreSQL role. As root they return empty and are read as legitimate
  zeros: green, having asserted nothing.
- **On the box, not from a laptop.** §10a signs in with a real `POST /staff/login`, which nginx
  rate-limits at `5r/m` and fail2ban watches at `maxretry=6`. The `geo` block exempts
  `127.0.0.1/32` for exactly this, and that only applies when the request originates here.

This replaces the `/etc/hosts` entry earlier revisions of this file prescribed. That workaround
made the suite address `*.localtest.me` on 127.0.0.1, which meant **the deploy gate exercised
Odoo and bypassed nginx entirely** — TLS, the catch-all, the rate limits and the
`X-Forwarded-Host` ProxyFix is gated on were all outside the only suite anyone ran. §8 and
`verify_edge.sh` are now an *addition* rather than a *compensation*.

---

## 2. Deploy a new commit

```bash
sudo MODRYN_DEMO_PASSWORD='<seeded password>' \
  /opt/modryn/deploy/scripts/deploy.sh <git-sha-or-tag>
```

What it does, and why each part is not optional:

1. Refuses to start unless last night's backup said `OK`. `-u all` writes schema; without a
   backup there is no way back from a bad migration.
2. `git checkout --detach <ref>`, reinstall requirements.
3. **Stops Odoo**, then upgrades **each tenant serially** with `-u all`. The platform is down
   for the whole loop. It prints elapsed time per tenant — watch that number; it is the honest
   running cost of database-per-tenant, and it only goes up.
4. Restarts Odoo, regenerates `tenants.map`, `nginx -t`, reloads nginx.
5. Runs `scripts/verify.sh`. **Any failure exits non-zero.**

Roll back: `sudo deploy.sh <previous-sha>` — the script prints the previous SHA before it
touches anything. A rollback is the same loop in reverse and takes the same downtime. If the
new code changed the schema in a way the old code cannot read, a rollback is a **restore**
(§5), not a checkout.

---

## 3. Add a boutique

```bash
sudo /opt/modryn/deploy/scripts/new_boutique_prod.sh <slug> "<Display Name>"
```

Slug: lowercase, starts with a letter, 2–31 characters. It becomes the database name **and**
the hostname label, so it must match the `server_name` regex in `modryn-site.conf`.

The script, in order: clones `modryn_template` with `createdb -T`; **checks the clone for both
uniqueness indexes and drops it if either is missing**; copies the filestore; regenerates
`database.uuid`, sets and freezes `web.base.url`, names the company and website; loads Twilio
credentials; appends the slug to `db_name`; regenerates `tenants.map`; `nginx -t` + reload;
restarts Odoo.

Three things worth knowing before you run it:

- **No downtime for the clone.** `createdb -T` needs zero connections to the source, and
  production keeps `modryn_template` **out of** `db_name` precisely so the running server holds
  none. Confirm any time with:
  ```bash
  sudo -u postgres psql -d postgres -tAc \
    "select count(*) from pg_stat_activity where datname='modryn_template'"   # must be 0
  ```
  If that is not 0, someone put the template back into `db_name` and adding a boutique now
  needs a restart.
- **The restart at the end is not optional.** Odoo parses `db_name` once, at startup. Until it
  restarts, the new tenant serves pages (dbfilter routes HTTP independently) but runs **no
  crons** — no SOS escalation, no SMS outbox drain, no reminders. Set `MODRYN_NO_RESTART=1` to
  batch several boutiques, then restart once.
- **DNS.** The wildcard `*.<DOMAIN>` A/AAAA record must already point at this box. There is no
  per-tenant DNS step.

Then seed staff for the new tenant (this is a decision about people, so it is not scripted):

```bash
sudo -u odoo MODRYN_DEMO_PASSWORD='<choose one>' \
  /opt/modryn/.venv/bin/python /opt/modryn/scripts/seed_staff.py
```

---

## 4. Back up

Automatic: `modryn-backup.timer`, nightly at **02:30 UTC** — clear of the 01:00–03:00 local DST
fold and outside boutique hours.

Manual: `sudo /opt/modryn/deploy/scripts/backup.sh`

Check it:

```bash
cat /var/log/modryn-backup.status          # one line: OK ... or FAILED ...
systemctl status modryn-backup.timer
ls -la /var/backups/modryn/daily/
```

The tenant list is read from `db_name` in `odoo.conf`, never a hard-coded array, so a boutique
added on Tuesday is in Tuesday night's set without anyone remembering. `modryn_template` is
appended explicitly, because production removes it from `db_name`.

`sessions/` is **not** backed up on purpose: restoring stale sessions is worse than making
everyone sign in again.

If `OFFSITE_TARGET` is unset the run reports a failure **every night**. That is intended.
Seven local dailies on one RAID1 pair survive a disk failure and not a `dropdb`, a filesystem,
or the machine.

**What offsite is, exactly.** Each clean run pushes **only that night's dated set** to
`$OFFSITE_TARGET/daily/<YYYY-MM-DD>/`, never with `--delete`, and then prunes the remote to the
newest **30** sets *by listing the remote directory*. Nothing about the push or the prune reads
the local tree, so:

| Failure | Offsite protects? |
|---|---|
| this machine dies / disk dies / filesystem eats itself | **yes** |
| `rm -rf /var/backups/modryn`, or ransomware encrypting it | **yes** — the remote keeps its 30 sets; tonight's push just adds one more |
| a bad local retention change pruning too far | **yes** — remote retention is computed remotely |
| a corrupt tenant dumped nightly for 31 days | **no** — the last good set has aged out |
| someone with the backup SSH key deleting the remote | **no** — that key is a credential, not a defence |

This was a `rsync -a --delete "$ROOT/" "$OFFSITE_TARGET/"` mirror until 2026-08-11, which meant
rows 2 and 3 of that table were **no**: a local wipe reached offsite on the next nightly run.
A mirror is not a backup, and calling it one is worse than not having it, because it retires
the worry that would otherwise have caught the gap.

Rows 4 and 5 are real and unfixed here. Row 4 is what the monthly restore drill is for. Row 5
needs append-only or immutable remote storage, which is an operator purchase, not a script.

A missed night makes the next run's `--link-dest` hardlink target absent: rsync warns and
copies in full. Space, not correctness.

---

## 5. Restore a tenant

```bash
sudo /opt/modryn/deploy/scripts/restore.sh bella /var/backups/modryn/daily/2026-08-09
```

Restores to **`bella_restore`**, never over the live database, and refuses outright if the
target name is listed in `db_name`. It then re-applies the same fixups `new_boutique_prod.sh`
applies — fresh `database.uuid`, `web.base.url`, `web.base.url.freeze` — because a restored
copy running alongside the original is exactly the "two tenants believing they are the same
instance" case those fixups exist to prevent, and an unfrozen base URL means a **test** restore
can put live-hostname links into real SMS.

Finally it **verifies**, in two kinds:

- **Must be non-empty** — both uniqueness indexes present, at least one installed `modryn_*`
  module, `res_users` and `res_partner` non-empty (Odoo base data guarantees rows in both, so
  zero means the restore lost them), `database.uuid` set.
- **Must be present and readable, count reported not judged** — `calendar_event` and
  `ir_attachment`. **A boutique that opened this week has no appointments**, and a tenant nobody
  has browsed yet has no attachments; treating zero as corruption made the operator's first real
  drill read as a failed backup. Zero here is legitimate; a *missing* table is not.
- Plus the one most often missed: the filestore is not empty **when attachment rows expect
  files**.

It also classifies `pg_restore`'s exit status instead of trusting it. `pg_restore` **cannot**
exit 0 here: the dump carries `COMMENT ON EXTENSION pg_stat_statements`, `COMMENT` requires
ownership, and that extension is not trusted so it must be owned by `postgres` while the restore
runs as `odoo`. Exactly one such error is expected and reported; any error that is *not* an
extension comment fails the restore.

Any failed check exits non-zero and tells you not to promote.

It stops there. **Promotion is a human decision.**

To promote `bella_restore` over `bella` (destructive, read twice):

```bash
systemctl stop odoo
sudo -u postgres psql -d postgres -c 'ALTER DATABASE bella RENAME TO bella_broken_YYYYMMDD;'
sudo -u postgres psql -d postgres -c 'ALTER DATABASE bella_restore RENAME TO bella;'
mv /var/lib/modryn/filestore/bella          /var/lib/modryn/filestore/bella_broken_YYYYMMDD
mv /var/lib/modryn/filestore/bella_restore  /var/lib/modryn/filestore/bella
# The restored copy's web.base.url still points at bella_restore.<DOMAIN>; fix it:
sudo -u odoo /opt/modryn/.venv/bin/python /opt/modryn/odoo/odoo-bin shell \
  -c /etc/odoo/odoo.conf -d bella --db-filter='^bella$' --no-http <<'PY'
env['ir.config_parameter'].sudo().set_param('web.base.url', 'https://bella.<DOMAIN>')
env['website'].search([], limit=1).domain = 'https://bella.<DOMAIN>'
env.cr.commit()
PY
systemctl start odoo
```

**Rename, never drop.** The broken database is the only evidence of what went wrong, and it
costs 80 MB to keep for a week.

### The drill

One full restore drill must pass **before the DNS flip**, and **monthly** after. An untested
backup is a belief, not a backup. Record each one:

| Date | Tenant | Dump age | Restore time | Checks | Notes |
|---|---|---|---|---|---|
| | | | | | |

---

## 6. Rotate certificates

certbot's own systemd timer renews and runs `--deploy-hook 'systemctl reload nginx'`. Confirm:

```bash
systemctl list-timers | grep certbot
certbot renew --dry-run
openssl s_client -connect bella.<DOMAIN>:443 -servername bella.<DOMAIN> </dev/null 2>/dev/null \
  | openssl x509 -noout -dates
```

If issuance ever moves, edit **`/etc/nginx/snippets/modryn-tls.conf`** — that file is the only
place the certificate paths appear, and both the catch-all and the tenant block include it.
nginx cannot take a variable in `ssl_certificate`, which is why it is a swappable include.

**HSTS is a commitment.** `includeSubDomains; max-age=31536000` is served on every tenant
response and cannot be un-said early. Every subdomain is HTTPS-only from the first visit,
permanently. That is intended.

---

## 7. Tune

Everything worth changing under load, and where:

| Symptom | Look at | File |
|---|---|---|
| p95 latency high, CPU not pinned | `workers` (48) | `/etc/odoo/odoo.conf` |
| CPU pinned before target throughput | **fewer** workers + faster templates, not more workers | `/etc/odoo/odoo.conf` |
| workers recycling constantly | `limit_memory_soft` (**bytes**, 2 GiB) vs. measured RSS | `/etc/odoo/odoo.conf` |
| connection refusals | `max_connections` (250) vs. ceiling `(48+4+1)×4 = 212` | `postgresql/tuning.conf` |
| SOS escalation late | full cron pass > 60 s → `max_cron_threads`, or fewer tenants per box | `/etc/odoo/odoo.conf` |
| 429s outside a ramp | limits too tight for Israeli CGNAT — raise them | `conf.d/modryn-http.conf` |

`workers` and `max_connections` are coupled. `db_maxconn` is **per process**, and prefork runs
cron workers and the gevent worker as separate processes. Raising `workers` without redoing
`(workers + max_cron_threads + 1) × db_maxconn` produces connection **refusals**, which look
like an application bug.

**Before a load campaign**, set `LOADGEN_IP` in `/etc/modryn/deploy.env` and re-run
`provision.sh`. Without it the campaign measures nginx's 429 rate and reports it as an Odoo
capacity ceiling. **Clear it again afterwards** and re-run `provision.sh`.

Server-side evidence:

```bash
tail -f /var/log/nginx/modryn.log         # rt= vs urt= : queueing vs. Odoo
sudo -u postgres psql -d bella -c \
  "select calls, mean_exec_time, rows, left(query,120) from pg_stat_statements
   order by total_exec_time desc limit 20;"
```

---

## 8. Verify the box

Run this after any nginx change. It is a script, not a checklist, because a checklist has no
exit code and nobody can tell afterwards whether it was run or skimmed.

```bash
sudo /opt/modryn/deploy/scripts/verify_edge.sh                      # on the box, all 12
DOMAIN=<D> TENANT=<slug> ./verify_edge.sh --remote-only             # from a laptop, 8 of 12
```

`deploy.sh` runs it automatically after reloading nginx. Twelve checks, each a defence with a
documented way of coming back if someone edits carelessly:

| | |
|---|---|
| E1 | unknown subdomain → static 404, no redirect chain, and **`ua_status=-` in `modryn.log` proving no upstream was contacted** — with a control proving the field is populated for a request that did reach Odoo |
| E2 | `/web/database/{manager,selector}` → 404 on the tenant host and an unknown host, bare and under `/en/` `/he/` `/ar/` |
| E3 | the prefixed routes answer **400**, not 404 — so E4 is not measuring a route that no longer exists |
| E4 | 12 CSRF-less POSTs to `/waitlist/join` and `/en/waitlist/join` → 429 in both shapes |
| E5 | port 8069 refused from outside |
| E6 | zero connections to `modryn_template` |
| E7 | gzip on the HTML **and** on the ~1 MB CSS bundle |
| E8 | `/web/filestore` → 404 (`internal`) |
| E9 | certificate chain verifies **and** has 30+ days left — the self-signed placeholder fails here |
| E10 | HSTS one year with `includeSubDomains`, `nosniff` |
| E11 | `fail2ban-regex` reports `1 matched` on a real journal line, and the captured host is the real client |
| E12 | `:80` → 301; the ACME path 404s from disk rather than redirecting |

`--remote-only` `skip()`s E1's log evidence, E6 and E11 **with the reason printed**, and the
summary says how many did not run — a green laptop run can never be mistaken for a full pass.

**Two checks are not read-only**, both bounded and documented at their call sites. E4 spends the
running client's own rate-limit bucket for about a minute (zero rows, zero SMS — no `csrf_token`
is sent, so Odoo rejects at 400 before any handler runs). E11 may generate **one** failed login,
1 of `maxretry=6` toward a one-hour ban of that IP on 80/443. Never loop it.

### What E1 replaces, and why

Earlier revisions of this section ran `journalctl -u odoo | grep nope` and expected no output.
**That check cannot fail.** `odoo.conf.prod` sets `log_level = warn` and the only handler override
is `res_users:INFO`, so production Odoo logs no request lines at all — the grep is empty whether
or not the request reached Python, including in the exact failure it existed to catch (unknown
subdomain → 303 → `/web/database/selector`, which raises nothing). Absence of evidence from a
logger that is switched off is not evidence of absence. `ua_status` in the nginx access log is a
positive fact about the request, and it carries its own control because `ua_status=-` is also what
a renamed log field produces.

**This is the check.** Expect `Lines: 1 lines, 0 ignored, 1 matched, 0 missed` and an
`Addresses found` line naming the client IP. `1 missed` means the filter is decorative and the
jail bans nobody, silently — which is the state this file shipped in until 2026-08-11.

If `grep` finds no line to feed it, generate one first (six wrong passwords will also test the
ban, so use one):

```bash
curl -sk -o /dev/null -d 'login=nosuchuser&password=wrong' "https://bella.$D/staff/login"
```

If there is still nothing in the journal, `log_handler = odoo.addons.base.models.res_users:INFO`
is missing from `odoo.conf` — `log_level = warn` suppresses that line on its own. If the line
shows `127.0.0.1` as the client, nginx is not sending `X-Forwarded-Host` and **the jail will ban
nginx** on the sixth failure, taking the whole platform down.

Then, and only then, the operational view:

```bash
sudo fail2ban-client status modryn-odoo
```

Rerun `fail2ban-regex` after any Odoo upgrade. The filter matches on the logger name and on the
`"Login failed for login:%s from %s"` message from
`odoo/odoo/addons/base/models/res_users.py`; both are Odoo's to change.

---

## 9. Monitor

netdata bound to `127.0.0.1`, reached over an SSH tunnel — never exposed, never behind a
password we have to manage:

```bash
ssh -L 19999:127.0.0.1:19999 root@<box>   # then http://localhost:19999
```

In priority order:

1. Odoo worker count vs. `workers` — constant recycling means `limit_memory_soft`; the symptom
   is latency, the cause is memory.
2. `pg_stat_activity` count vs. 250 — approaching it means refusals, not slowness.
3. 5xx rate from `/var/log/nginx/modryn.log`.
4. p95 on `/shop`, `/book`, `/floor` — one customer path, one booking path, one staff path.
5. nginx 429 count — non-zero outside a ramp means the limits are too tight for real traffic.
6. Cron pass duration against the 1-minute SOS interval.
7. Age of `/var/log/modryn-backup.status`.

Plus an **external** uptime check on `https://bella.<DOMAIN>/shop` **and** on a deliberately
unknown subdomain that must return 404. The second one is the regression test for the
catch-all, and it is what would catch the database selector coming back after an nginx edit.

---

## 10. Emergencies

**Site down.**
```bash
systemctl status odoo nginx postgresql
journalctl -u odoo -n 200 --no-pager
systemctl restart odoo
```
Customers see the static Hebrew 5xx page meanwhile; it references no `/web/assets/`, which is
exactly what is unavailable when it fires.

**Locked out by fail2ban** (this bans nginx if `X-Forwarded-Host` ever goes missing):
```bash
fail2ban-client status modryn-odoo
fail2ban-client set modryn-odoo unbanip <ip>
fail2ban-client stop modryn-odoo          # only while you fix the header
```

**Disk full.** Usual suspects, in order: `/var/backups/modryn`, `/var/lib/modryn/sessions`
(4096 directories of session files), journald.
```bash
du -sh /var/backups/modryn /var/lib/modryn/* /var/log/journal
journalctl --vacuum-size=500M
```
Never delete from `/var/lib/modryn/filestore` — those are the only copies of the attachments
the database rows point at.

**A tenant is corrupt.** Restore it (§5). Its neighbours are untouched; that isolation is the
whole reason for database-per-tenant and the reason dumps are per-tenant files.

---

## 11. Before the DNS flip

| # | Gate | How to check |
|---|---|---|
| 1 | `scripts/verify.sh` — 0 failed, **through nginx** | exit 0, invoked per §1e |
| 2 | Unknown subdomain 404s statically and no upstream was contacted | `verify_edge.sh` E1 |
| 3 | `/web/database/` 404s on every host, **prefixed and unprefixed** | `verify_edge.sh` E2 |
| 3b | SMS endpoints return 429 under a language prefix too | `verify_edge.sh` E3 + E4 |
| 3c | `fail2ban-regex` reports `1 matched`, capturing the real client | `verify_edge.sh` E11 |
| 4 | 0 connections to `modryn_template` while serving | `verify_edge.sh` E6 |
| 5 | One full restore drill passed and logged | §5 |
| 6 | Fonts self-hosted — **no** request to `fonts.gstatic.com` on any page | `qa/specs/fonts.spec.js`, or the four-path probe in §12 |
| 7 | One SMS delivered to a real second handset | operator-owned |
| 8 | Twilio credentials rotated (the current ones were pasted into a transcript) | Twilio console |
| 9 | Ramp campaign done, tuning log filled in | §7 |
| 10 | Rate limits are not so tight that real traffic trips them | **two numbers, below** |

### Gate 10, restated so it can fail

The old form was `grep ' 429 ' /var/log/nginx/modryn.log`, and it **matches nothing on any box** —
`log_format modryn_load` writes `status=$status`, so the line reads `status=429`, never
space-429-space. The gate was passing on grep syntax.

Fixing the grep is not enough. §7 tells you to exempt `LOADGEN_IP` from `limit_req`, which makes
the generator *structurally incapable* of producing a 429; without the exemption a single-IP
generator against `rate=10r/m` produces thousands from the first stage. There is no configuration
in which "zero 429s from the load generator" is both achievable and informative.

What the gate is actually trying to assert is *the limits are not so tight that real traffic trips
them*. Two numbers, and neither means anything alone:

```bash
# (a) 429s from anyone who is NOT the exempted generator, during the ramp window.
#     Non-zero means the limits are too tight for real traffic and must be raised —
#     which is what modryn-http.conf's own comment already says.
awk -v since="$RAMP_START" '$1 >= since && /status=429/' /var/log/nginx/modryn.log | wc -l   # 0

# (b) THE CONTROL, without which (a) is worthless: prove 429 is reachable at all.
#     A limit_req that was deleted, or a zone that never matched a location,
#     produces the same zero.
sudo /opt/modryn/deploy/scripts/verify_edge.sh        # E4 must pass, same session
```

Gate 10 = (a) is zero **and** (b) passed in the same session. One without the other is the
"healthy jail with zero bans" mistake in a different costume.

Note that `modryn_load` carries no `$remote_addr`, so (a) cannot filter the generator out by IP.
Either add `client=$remote_addr` to the format — a versioned change, since the format is a
declared contract with the load harness — or read `/var/log/nginx/access.log` for that one number.
Flagged rather than silently chosen.

Gates 6, 7 and 8 are not fixable from this directory. Gate 6 in particular is a code change in
`addons/modryn_theme` — the storefront currently pulls Frank Ruhl Libre and Assistant from
Google's CDN, which is a third-party dependency on every cold page load, a privacy exposure
under EU and Israeli law, and a load campaign that measures Google's latency instead of ours.

---

## Operator-owned items

Nothing in this list can be done from the repository.

1. The box itself (16c/32t, 128 GB assumed by `postgresql/tuning.conf` — **confirm before
   applying `shared_buffers = 24GB`**)
2. Domain registration and DNS control
3. A DNS API token scoped to `_acme-challenge` TXT records
4. Wildcard `A`/`AAAA` record `*.<DOMAIN>` → box IP
5. The `admin_passwd` plaintext, in a password manager
6. Admin SSH public keys (`PasswordAuthentication no`, `PermitRootLogin no`)
7. Offsite storage box + SSH key for `OFFSITE_TARGET`
8. External uptime monitoring account
9. The load generator's source IP, for `LOADGEN_IP`
10. **Rotating the Twilio credentials** exposed in the 2026-08-10 transcript
11. A destination mobile number to prove one SMS end to end

---

## Where the spec was wrong

Recorded because each one would have produced a real production failure, and because these
artifacts implement the correction rather than the spec.

1. **The fail2ban filter regex matches nothing.** The spec's
   `Login failed for db:\S+ login:\S+ from <HOST>` has no counterpart in Odoo 19 —
   `odoo/odoo/addons/base/models/res_users.py` logs `"Login failed for login:%s from %s"`, with
   no `db:` field. The jail would report healthy and ban nobody.
2. **`log_level = warn` silences the line the jail depends on.** `warn` maps to `odoo:WARNING`,
   and the login-failure line is INFO on a child of `odoo`. The spec's `log_handler = :INFO`
   sets the **root** logger, which the explicit `odoo:WARNING` overrides for every Odoo logger.
   `odoo.conf.prod` sets `log_handler = odoo.addons.base.models.res_users:INFO` instead, which
   is applied last and wins.
3. **The rate-limit block does not cover the token routes it lists.** §4.7's table names
   `/claim/<token>` and `/b/<token>/confirm|cancel`, but the nginx snippet below it uses only
   `location =` exact matches, which cannot match a path containing a token. Those three are
   regex locations here. `/my/cancel/<id>` — also `auth='public'`, also `POST`, also mutates a
   booking, and guessable by integer id — was missing from the table entirely and is limited too.
4. **`proxy_set_header` does not accumulate across levels.** The spec sets the forwarded headers
   at http level and then adds `Upgrade`/`Connection` inside `location /websocket`. nginx
   inherits `proxy_set_header` from the enclosing level *only if the current level sets none*,
   so that location silently loses `X-Forwarded-Host` — which is the exact header ProxyFix is
   gated on. The websocket location here repeats all of them.
5. **The template database has to be created out of band, and one of its three extensions
   needs a superuser.** Odoo only creates databases from the database *manager*, which
   `list_db = False` switches off; `odoo-bin -d <name> -i <mods>` fails on a missing database
   rather than creating one. `build_template_prod.sh` therefore issues Odoo's exact
   `CREATE DATABASE ... ENCODING 'unicode' LC_COLLATE 'C' TEMPLATE template0` itself, after
   which every tenant is a `createdb -T` clone that needs no privilege at all.
   Of the extensions, **`pg_trgm` and `unaccent` are trusted** in PostgreSQL 16 and are created
   by the `odoo` role, which owns the database; only **`pg_stat_statements` is not trusted** and
   needs `postgres`. (An earlier revision of this list claimed `pg_trgm` was untrusted and that
   Odoo would fail on it — measurably false on both counts:
   `select name, trusted from pg_available_extension_versions` reports `pg_trgm t`, and
   `odoo/odoo/service/db.py` wraps that `CREATE EXTENSION` in a `try/except` that only logs a
   warning. Ownership is the part that actually matters, because `pg_dump` emits
   `COMMENT ON EXTENSION` and `COMMENT` requires it — see §5.)
6. **Adding a tenant to `db_name` requires a restart, not a reload.** Odoo parses the config
   once at startup, so a newly provisioned tenant serves pages immediately (dbfilter is
   independent) but runs **no crons** — including the 1-minute SOS escalation — until the
   service restarts. The spec's provisioning story does not say this.
7. **`verify.sh` cannot be pointed at production hostnames.** Launch gate 1 says to run it
   "against the production box", but the script hardcodes `bella.localtest.me` and
   `noga.localtest.me` with no environment override. §1e gives the `/etc/hosts` workaround, and
   notes the consequence: the suite then exercises Odoo directly and bypasses nginx entirely, so
   the nginx gates in §8 are not optional extras.
8. **Ubuntu 24.04 ships nginx 1.24**, where `http2 on;` does not exist and `nginx -t` fails on
   it. The site config uses `listen ... http2` and says why not to "modernise" it.
9. **nginx needs filesystem access to the filestore.** `x_sendfile = True` makes nginx read
   `/var/lib/modryn/filestore` directly, but the spec's security table has `data_dir` owned by
   `odoo` alone. `provision.sh` grants `www-data` traverse+read via ACLs rather than loosening
   the directory mode, and `new_boutique_prod.sh` and `restore.sh` reapply it per tenant.
10. **`robots.txt`.** §4.8 says leave it proxied so each boutique controls her own — correct,
    and the tenant block has no `/robots.txt` location. The `robots.txt` shipped here is served
    **only by the catch-all**, where there is no website to control it.

### Found on 2026-08-11, in these artifacts rather than in the spec

Each of these shipped in this directory and was confirmed against the running instance.

11. **A language prefix removed every rate limit from the SMS endpoints.** Every MODRYN route is
    `website=True`, so Odoo answers at `/path` *and* `/<lang>/path` — and renders the prefixed
    form into its own markup:
    `curl -H 'Accept-Language: en-US' .../en/book` returns `<form action="/en/book/submit">`.
    For a POST, `http_routing` sets `allow_redirect = False` and reroutes internally, so there
    is no 3xx for nginx to see. The `location = /book/submit` blocks matched the bare form only,
    and `/book/submit` and `/waitlist/join` each send an SMS. The application's only throttle is
    per-phone-number, which does not bound an attacker rotating numbers, so `limit_req` was the
    entire defence for the Twilio bill and the prefixed form bypassed all of it. Every
    rate-limited location is now `~ ^(/[^/]+)?/...$` — one optional leading segment, not a list
    of language codes, because a hardcoded `(en|he|ar)` drifts the day an owner enables French,
    and drifts in the direction that removes the limit.
12. **`/web/database/` was bypassable the same way.** `location ^~ /web/database/` does not cover
    `/en/web/database/manager`, which Odoo serves as the byte-identical page (verified with
    `curl` + `cmp`: two 46,329-byte responses). Now covered by a regex location alongside the
    `^~` one. `list_db = False` already refuses the operations themselves, so this is defence in
    depth, not the difference between safe and remotely destroyable — `odoo.conf.prod` used to
    overstate it and now says which layer does what.
13. **The fail2ban filter matched nothing, and the check for it proved nothing.** `^Login failed`
    anchors at the start of a line that actually begins with journald's host and identifier
    followed by Odoo's `asctime pid level dbname logger:` prefix. Reconstructed from
    `odoo/odoo/netsvc.py`'s format string, no line ever started with that text. The old §8 check
    grepped for `Login failed` (present) and printed jail status (healthy, zero bans) — a result
    indistinguishable from "nobody attacked us". §8 check 7 now runs `fail2ban-regex` against a
    real journal line and requires `1 matched`.
14. **`--stop-after-init` cannot run while Odoo is serving.** `PreforkServer.run()` calls
    `self.start()` — which binds `http_port` — *before* `preload_registries()`, so with
    `workers > 0` a template rebuild or an upgrade run on a live box dies at `socket.bind()`
    with `EADDRINUSE`. Day-one provisioning survived only because the runbook builds the template
    before starting Odoo; a rebuild, which `new_boutique_prod.sh` instructs mid-provisioning, hit
    it every time — and `build_template_prod.sh` refuses to run twice, leaving a half-built
    template to drop by hand. Every `odoo-bin` invocation in this directory that must not listen
    now passes `--no-http`.
15. **`restore.sh` failed every restore, twice over.** Its `pg_restore` ran under `set -e` and
    `pg_restore` cannot exit 0 here (`COMMENT ON EXTENSION` needs ownership), so the script died
    before the filestore copy and before every verification check; and `pg_stat_statements`,
    which `build_template_prod.sh` puts in every tenant, was not pre-created, so the restore also
    took a hard `permission denied to create extension` as the non-superuser `odoo` role. Its
    verifier separately treated a count of `0` as failure, which made a legitimate restore of a
    boutique with no bookings yet read as a corrupt backup.
16. **"Offsite" was an `rsync --delete` mirror.** A local `rm -rf` of the backup root propagated
    offsite on the next nightly run. It now pushes only the current dated set, additively, and
    prunes the remote by age from the remote's own listing. See §4 for the table of what that
    does and does not protect against.
