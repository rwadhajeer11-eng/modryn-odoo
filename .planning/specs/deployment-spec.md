# Production deployment specification

_Written 2026-08-10 against commit `ae84376`. Every number, path and directive below was
checked against the running instance or against the Odoo 19 clone at `odoo/`. Where a claim
could not be verified it was dropped, not softened._

This document describes how MODRYN-on-Odoo goes to production: one machine, bare systemd,
self-hosted PostgreSQL 16, nginx in front. It is written to be executed from without asking
questions.

**Read `odoo.conf` first.** Every comment in it records a real failure. This spec keeps all of
them and adds the ones that only appear under load, behind a proxy, or with a second tenant
being provisioned while the server is running.

Anything that needs a purchase, an account, or a judgement call that is not ours is marked
**USER-OWNED** and listed together in §10.

---

## 0. What is being deployed

| | |
|---|---|
| Application | Odoo 19.0 **Community**, shallow clone at `odoo/`, never edited |
| Custom code | seven addons in `addons/`, ~6,400 non-blank lines |
| Tenancy | one PostgreSQL database per boutique, routed by `dbfilter = ^%d$` |
| Tenant footprint | measured: `modryn_template` 73 MB, `bella` 80 MB + 16 MB filestore, `noga` 79 MB + 2.2 MB filestore |
| Runtime deps | Python 3.12, `rtlcss` (npm, global), `rlPyCairo` (not in `odoo/requirements.txt`) |
| Provisioning | `scripts/new_boutique.sh` — `createdb -T modryn_template` + filestore `cp -R` + four per-tenant fixups |
| Regression gate | `scripts/verify.sh` — 85 checks, must be 0 failed before and after every deploy |

Two runtime dependencies are easy to lose in a fresh provision and both fail *silently*:

- **`rtlcss`** — Odoo generates RTL stylesheets by running `rtlcss` over the compiled LTR CSS.
  Without it a Hebrew storefront renders LTR-ish. `verify.sh` §2 catches it.
- **`rlPyCairo`** — needed for QR rendering (`/queue/sign`), needs `pkg-config` + `cairo` at
  build time, and is **not** in `odoo/requirements.txt`. `verify.sh` §6 catches it.

---

## 1. Hosting decision

### The decision

One **Hetzner AX102-class dedicated box** — 16c/32t, 128 GB ECC, 2× NVMe in RAID1 — running
bare systemd units. PostgreSQL 16 self-hosted on the same box, reachable over the unix socket
only. No Docker, no container runtime, no orchestrator.

**USER-OWNED:** the Hetzner account, the box order, and the AX102 tier itself. The arithmetic in
§2 assumes 16 physical cores and 128 GB; a smaller tier moves every number.

### Why one box

This is one Python application, one database, and a filestore that the two must share. There is
no second service to schedule, nothing to bin-pack, and no team that needs an abstraction between
them. A single machine is the honest shape of the system.

### Why no Docker

The usual argument for containers here is reproducibility. That argument is weaker than it looks
for this workload:

- **Reproducibility already has a cheaper home.** `provision.sh` (§8) is a single idempotent
  shell script, the same shape as the `bootstrap.sh` that already works. It is *less* machinery
  than a Dockerfile plus a build pipeline plus a registry plus a compose or unit file plus two
  named volumes.
- **Two volumes are not incidental, they are the whole state.** The filestore
  (`<data_dir>/filestore/<db>/`) and the sessions directory (`<data_dir>/sessions/`, a
  `FilesystemSessionStore` scattering session files across 4096 subdirectories — `odoo/http.py:995`)
  both live on disk and both must survive a deploy. Containerising the app means mounting both
  back in, at which point the container has bought nothing and cost a layer.
- **Odoo's own process model resists it.** With `workers > 0` Odoo runs a `PreforkServer` that
  forks HTTP workers, cron workers, *and* spawns a gevent subprocess for websockets
  (`odoo/service/server.py:944, 1042-1047`). That is a process tree with a supervisor inside the
  container, which is exactly the shape containers are worst at.

systemd gives us restarts, resource limits, log capture, ordering, and socket readiness for free,
and it is already on the box.

### Why not managed PostgreSQL

`scripts/new_boutique.sh` is the tenancy story, and it depends on two things a managed provider
does not give you together:

1. **`createdb -T modryn_template`.** Template cloning is a server-local operation. RDS, Cloud SQL
   and Hetzner's managed offerings either forbid `CREATE DATABASE ... TEMPLATE` from a
   non-superuser or make it awkward enough that the ~20-second provision becomes a dump/restore
   measured in minutes.
2. **A filestore copy that must be atomic with the clone.** The script does
   `cp -R "$FILESTORE/$TEMPLATE" "$FILESTORE/$SLUG"` immediately after `createdb -T`, because a
   cloned database points at filestore rows that would otherwise resolve to nothing. The database
   and the filesystem have to be on the same side of the same operation.

Managed Postgres also puts the database behind TCP and a network hop that we do not need, and
takes away the unix socket that §7 relies on for its security posture.

### Staging: buy production now, flip DNS later

Buy the production box today. Run the entire ramp campaign against it under a staging hostname.
When the campaign is done and the tuning log (§9) is filled in, flip DNS to it.

This gives a staging environment that is not *like* production — it *is* production — at zero
double spend. The alternative (a smaller staging box) measures a machine we are not going to run,
which is the failure mode that makes load testing theatre.

The consequence to accept: there is no separate staging box after launch. The first post-launch
change that needs one is the moment to buy a second, smaller machine — not before.

---

## 2. Sizing arithmetic

Numbers below are derived, not asserted. Each line shows its working so a different target moves
the result predictably.

### 2.1 Offered load

The target is 10,000 concurrent users. Concurrency is not a request rate; the conversion is think
time.

```
offered dynamic rate  R = users / think_time

  think_time = 25 s  ->  R = 10,000 / 25 = 400 req/s
  think_time = 50 s  ->  R = 10,000 / 50 = 200 req/s
```

A browse-and-book session on a bridal storefront is read-heavy with long pauses — reading a dress
page, choosing a size, filling a booking form. 25–50 s is the defensible band, so **R = 200–400
dynamic requests/second**.

Static assets do not enter this number. Odoo serves `/web/assets/<website_id>/<hash>/<bundle>.min.css`
with a content hash in the path and — measured on the running instance —
`Cache-Control: public, max-age=31536000, immutable`. A returning visitor fetches zero bytes of
CSS or JS from the origin. A *cold* visitor fetches ~1 MB (the frontend CSS bundle alone is
**995,309 bytes** uncompressed), which is a gzip problem, not a worker problem — see §4.6.

### 2.2 HTTP workers

Little's Law: `workers = R × S`, where `S` is mean service time for a dynamic request.

| S (mean service time) | workers for R=400 | workers for R=300 |
|---|---|---|
| 80 ms | 32 | 24 |
| 100 ms | 40 | 30 |
| 150 ms | 60 | 45 |

A queue driven at 100 % utilisation has unbounded latency, so size for ~70–85 %, not 100 %.

**Choose `workers = 48`.** At S = 100 ms that is 480 req/s of capacity, 83 % utilised at the top
of the band and 62 % at R = 300. At S = 150 ms it is 320 req/s and 48 workers is *not* enough for
R = 400 — which is precisely what the ramp campaign is for. `workers` is the first row of the
tuning log.

Memory:

```
48 HTTP workers  × ~300 MB RSS  = 14.4 GB
 4 cron workers  × ~300 MB RSS  =  1.2 GB
 1 gevent worker × ~500 MB RSS  =  0.5 GB
                                  -------
                       app total ≈ 16.1 GB
```

300 MB/worker is a **planning figure, not a measurement.** The dev instance measures 51 MB RSS,
but that is a threaded server with three registries and no load; a prefork worker with the
`website` + `website_sale` registries hot under concurrency is far larger. Replace this row with a
measurement during the ramp.

CPU sanity check: 48 workers on 32 hardware threads is 1.5× oversubscription. Odoo workers spend
real time blocked on Postgres, so oversubscription is normal — but QWeb rendering is CPU-bound, so
the hard ceiling is `32 / S_cpu`. If S_cpu is 60 ms that is 533 req/s, consistent with the target.
If the ramp shows CPU pinned at 100 % before 400 req/s, the answer is fewer workers and faster
templates, not more workers.

### 2.3 The gevent (websocket) process

**One gevent process, and it is not a second systemd unit.** `PreforkServer` spawns it itself via
`long_polling_spawn()` (`odoo/service/server.py:944`) as `odoo-bin gevent`, automatically, whenever
`workers > 0`. Writing a second unit file for it is a mistake.

One process is enough, and this can be shown rather than assumed. Only two client components
subscribe to the bus:

| Component | Surface | Auth |
|---|---|---|
| `addons/modryn_staff/static/src/floor/floor_board.js` | `/floor` dispatch board | `auth='user'` |
| `addons/modryn_queue_poc/static/src/queue_board/queue_board.js` | backend `ir.actions.client` | back office |

Both are staff-only. **No customer surface holds a websocket** — not `/shop`, not `/book`, not
`/q/<token>`, not `/my/bookings`. Websocket concurrency is therefore bounded by *staff on shift*,
not by shoppers:

```
20 boutiques × 10 staff = 200 concurrent websockets
```

One gevent process carries that without noticing. The number to watch as tenants grow is
websockets, not users.

### 2.4 PostgreSQL

| Setting | Value | Reason |
|---|---|---|
| `shared_buffers` | `24GB` | ~19 % of 128 GB. Below the conventional 25 % on purpose — see the data-volume note below, the working set fits regardless, so the RAM is better spent on page cache. |
| `effective_cache_size` | `64GB` | Planner hint only, allocates nothing. Conservative given ~80 GB is genuinely free after §2.2's 16 GB app and 24 GB shared_buffers. |
| `max_connections` | `250` | Bounds the ceiling computed below with room for psql, backups and netdata. |
| `work_mem` | `16MB` | Per sort/hash node, not per connection. Worst case ≈ 250 conns × 16 MB × 2 nodes = 8 GB, which fits. |
| `maintenance_work_mem` | `2GB` | Makes the nightly `VACUUM`/`ANALYZE` and any `REINDEX` finish in a window rather than a night. |
| `random_page_cost` | `1.1` | NVMe. The 4.0 default models a spinning disk seek that does not exist here and pushes the planner to sequential scans it should not choose. |
| `effective_io_concurrency` | `200` | NVMe queue depth. |
| `wal_compression` | `on` | Cheap on a 16-core box, and it shrinks the base backups in §5. |
| `checkpoint_completion_target` | `0.9` | Spreads checkpoint I/O instead of stalling. |

**USER-OWNED:** none of the above; but confirm the box actually ships 128 GB before applying
`shared_buffers = 24GB`.

#### The connection ceiling — corrected

The brief this spec answers put the ceiling at `workers × db_maxconn = 48 × 4 = 192`. **That
undercounts.** `db_maxconn` is a *per-process* pool — `odoo/sql_db.py:621` builds a module-global
`_Pool = ConnectionPool(int(maxconn))`, one per OS process — and prefork mode runs more processes
than just the HTTP workers:

```
 48 HTTP workers   × 4 = 192     (odoo/service/server.py:1041)
  4 cron workers   × 4 =  16     separate processes, not threads (server.py:1045)
  1 gevent worker  × 4 =   4     db_maxconn_gevent defaults to db_maxconn
                        -----
                 ceiling = 212
```

Against `max_connections = 250`, minus PostgreSQL's default `superuser_reserved_connections = 3`,
that leaves **35 connections** for psql, `pg_dump`, netdata and a human. Workable, and tighter
than 192 implied. Do not raise `workers` without re-running this line.

Steady-state usage is lower than the ceiling: cron workers call `sql_db.close_db()` between passes
when more than one database is in play (`server.py:1467-1469`). The ceiling is what
`max_connections` must survive, not what you will see in `pg_stat_activity`.

> **Trap for later.** If anyone ever sets `db_replica_host`, `Registry.__init__` creates a *second*
> pool per process (`odoo/orm/registry.py:254-257`, guarded by the comment "by default, only use
> readonly pool if we have a db_replica_host defined"). Every process then holds up to
> `2 × db_maxconn` and the ceiling doubles to **424**, blowing `max_connections` on the first busy
> minute. Adding a read replica is a `max_connections` change, not just a config line.

#### Data volume — why this workload is CPU-bound

Measured today:

| Database | Size | Filestore |
|---|---|---|
| `modryn_template` | 73 MB | 2.0 MB |
| `bella` | 80 MB | 16 MB |
| `noga` | 79 MB | 2.2 MB |

At ~80 MB per tenant, **100 boutiques is ~8 GB** — a third of `shared_buffers`. The entire
multi-tenant working set lives in RAM at any plausible tenant count. Postgres is not the
bottleneck here and will not become one from growth alone; Python request handling is. Spend
tuning effort on §2.2, not on the storage layer.

### 2.5 Cron capacity — the real tenant ceiling

Six crons ship in the addons. The tightest is one minute:

| Cron | Interval | Source |
|---|---|---|
| SOS escalation | **1 minute** | `addons/modryn_staff/data/ir_cron_data.xml` |
| SMS outbox drain | 5 minutes (+ `_trigger()`) | `addons/modryn_portal/data/ir_cron_data.xml` |
| Waitlist offer expiry | 10 minutes | `addons/modryn_portal/data/ir_cron_data.xml` |
| Booking reminders | 15 minutes | `addons/modryn_portal/data/ir_cron_data.xml` |
| OTP garbage collection | 6 hours | `addons/modryn_portal/data/ir_cron_data.xml` |
| Ticket expiry | 1 day | `addons/modryn_queue_poc/data/ir_cron_data.xml` |

The outbox drain is the one whose *interval* misleads: it is `_trigger()`-driven, so the 5 minutes
is a floor for retries, not the delivery latency. See the `pg_notify` note below — that mechanism
is what makes a confirmation SMS leave within about a second of the booking commit.

A cron worker processes **one database per call** (`server.py:1461-1465`). With
`max_cron_threads = 4`, four databases are in flight at once. For the 1-minute SOS cron to mean
one minute, a full pass over every tenant must complete inside 60 seconds. That — not request
throughput — is what bounds tenant count on a single box, and it is the second thing the ramp
campaign should measure.

Cron double-firing is *not* a concern: `ir_cron` claims jobs with
`FOR NO KEY UPDATE SKIP LOCKED` (`odoo/addons/base/models/ir_cron.py:330, 365`), a database-level
lock that holds across processes and across hosts.

`ir.cron._trigger()` fires `pg_notify('cron_trigger')` post-commit
(`odoo/addons/base/models/ir_cron.py:800`) and the cron workers `LISTEN cron_trigger`
(`server.py:1493`), so an outbox drained by cron wakes within about a second. No queue_job, no
Enterprise.

### 2.6 Horizontal fallback — documented, not built

If one box stops being enough, a second **app** box (Postgres stays single) needs three things,
and two of them are consequences of state living on the filesystem:

1. **Shared filestore.** Attachments live at `<data_dir>/filestore/<db>/<sha[:2]>/<sha>`. Two boxes
   with separate disks serve half the images each. Needs NFS, or an object-storage backend that
   does not exist in Community today.
2. **Sticky sessions.** Sessions are a `FilesystemSessionStore` (`odoo/http.py:995`) under
   `<data_dir>/sessions/`, scattered across 4096 directories. A session file exists on exactly one
   box, so round-robin produces random logouts. nginx must hash on the `session_id` cookie (or
   `ip_hash` as the cruder version).
3. **`max_cron_threads = 0` on the second box.** Not for correctness — the `SKIP LOCKED` claim
   above makes concurrent cron safe — but to stop two boxes polling every tenant database every
   minute for work only one of them will get.

The honest summary: this is a real day of work and a new failure mode (shared filesystem), and it
buys app CPU only. Do it when the tuning log says CPU is the wall, not before.

---

## 3. Production `odoo.conf`, directive by directive

Path: `/etc/odoo/odoo.conf`, owner `root:odoo`, mode `0640` — it contains `admin_passwd`.

```ini
[options]
; --- Where Odoo finds code -------------------------------------------------
; Core first, our addons second. Odoo core is NEVER edited; everything we add
; lives in ./addons and reaches core through model/view inheritance. That is
; what makes a version upgrade survivable.
addons_path = /opt/modryn/odoo/addons,/opt/modryn/addons

; --- Runtime data ----------------------------------------------------------
; Holds filestore/ AND sessions/. Both are live state and both must survive a
; deploy; neither is in the repo. This is the directory §5 backs up and the
; one nginx X-Accel-aliases in §4.5.
data_dir = /var/lib/modryn

; --- Database --------------------------------------------------------------
; db_host is a STRING in Odoo 19 (`False` logs a warning and is ignored).
; Empty makes libpq use the local unix socket, which is the only thing
; PostgreSQL listens on in production (§7).
db_host =
db_user = odoo

; The ONLY databases this instance may touch. Without it, Odoo's cron
; enumerator falls back to listing every database on the server —
; `cron_database_list()` is literally `config['db_name'] or list_dbs(True)`
; (odoo/service/server.py:99-100) — and errors against each one it has no
; business opening. dbfilter routes HTTP; db_name bounds the crons.
;
; TENANTS ONLY. modryn_template is deliberately absent: anything listed here
; gets a persistent connection held open, and new_boutique.sh aborts when
; pg_stat_activity shows ANY connection to the template, because `createdb -T`
; requires zero. Listing the template here means provisioning a boutique needs
; a server restart. Template work passes `-d modryn_template` explicitly,
; which overrides this line.
db_name = bella,noga

; THE TENANCY ROUTER. %d is the first hostname label, so
; bella.modryn.co.il -> database "bella". One PostgreSQL database per
; boutique: isolation by construction, no RLS needed. Unchanged from dev.
dbfilter = ^%d$

; Kills the database manager: create/drop/duplicate/backup/restore all refuse
; (odoo/service/db.py:44-51, list_dbs raises AccessDenied at db.py:435).
; NOTE this does not UNREGISTER the routes — see §4.3.
list_db = False

; pbkdf2_sha512 hash, never a plaintext password. Odoo verifies hashes
; natively (odoo/tools/config.py:21-23, verify_admin_password at :1037).
; Generate with the command below this block.
admin_passwd = $pbkdf2-sha512$600000$...          ; USER-OWNED — generate, do not reuse

; Per-process pool size. Ceiling arithmetic in §2.4:
; (48 http + 4 cron + 1 gevent) x 4 = 212 against max_connections 250.
; Raising this without raising max_connections produces connection refusals
; under load, not slowness.
db_maxconn = 4

; --- HTTP ------------------------------------------------------------------
; Bind to loopback ONLY. nginx is the sole ingress; a public bind would let a
; direct request to :8069 bypass every rule in §4, including the /web/database
; block and the rate limits.
http_interface = 127.0.0.1
http_port = 8069

; The websocket port. PreforkServer spawns the gevent process itself
; (server.py:944) whenever workers > 0 — do NOT write a second systemd unit.
gevent_port = 8072

; Applies werkzeug's ProxyFix (x_for=1, x_proto=1, x_host=1, odoo/http.py:190).
; ONLY safe because http_interface is loopback and nginx is the only client.
; CRITICAL: this is a no-op unless nginx sends X-Forwarded-Host — the whole
; block is gated on `environ.get("HTTP_X_FORWARDED_HOST")` at http.py:2834.
; Without that header Odoo thinks every request arrived over plain HTTP from
; 127.0.0.1, and every log line and generated URL is wrong.
proxy_mode = True

; --- Processes -------------------------------------------------------------
; Sized in §2.2. First row of the tuning log; expect to change this.
workers = 48

; Four cron worker PROCESSES (not threads) in prefork mode (server.py:1045).
; Sized against the 1-minute SOS cron in §2.5, not against request load.
max_cron_threads = 4

; --- Limits ----------------------------------------------------------------
; Seconds of CPU per request. A request that burns 120s of CPU is a bug, and
; without this it takes a worker with it.
limit_time_cpu = 120

; Seconds of wall clock per request. Must exceed limit_time_cpu or the CPU
; limit can never fire.
limit_time_real = 240

; Crons get their own, longer budget: a reminder sweep across every tenant is
; legitimately slower than any web request. -1 (the default) would make crons
; inherit limit_time_real and kill a legitimate sweep at 240s.
limit_time_real_cron = 600

; BYTES, not megabytes. The defaults are 2048*1024*1024 and 2560*1024*1024
; (odoo/tools/config.py:465,475). Writing `limit_memory_soft = 2048` sets a
; 2 KB limit and every worker dies on startup. Soft = recycle after the
; current request; hard = fail the next allocation.
limit_memory_soft = 2147483648
limit_memory_hard = 2684354560

; Recycle a worker after this many requests, to cap slow leaks. Also bounds
; the cron worker's database queue: it logs an error when there are more
; databases to process than limit_request allows (server.py:1471-1476), which
; only matters if tenant count ever approaches this number.
limit_request = 65536

; --- Static delivery -------------------------------------------------------
; Emits X-Accel-Redirect so nginx serves filestore bytes from disk and the
; Python worker is freed immediately (odoo/http.py:669-681). Requires the
; `internal` location in §4.5; without it every attachment 404s.
x_sendfile = True

; --- Logging ---------------------------------------------------------------
; No logfile: stdout is captured by journald via the systemd unit, so log
; rotation, retention and querying are the OS's problem, not ours.
log_level = warn
log_handler = :INFO
```

Generate the `admin_passwd` hash (USER-OWNED — run it, store the plaintext in a password
manager, put only the hash in the file):

```bash
/opt/modryn/.venv/bin/python - <<'PY'
from passlib.context import CryptContext
import getpass
ctx = CryptContext(schemes=['pbkdf2_sha512'], pbkdf2_sha512__rounds=600_000)
print(ctx.hash(getpass.getpass('master password: ')))
PY
```

An **empty** `admin_passwd` refuses authentication outright (`verify_admin_password` returns
`False` on an empty stored hash, `odoo/tools/config.py:1037-1042`), which is stricter still. We
keep a hash rather than an empty value so that a deliberate, documented recovery path exists —
`list_db = False` plus the nginx 404 in §4.3 already make the manager unreachable in normal
operation.

### 3.1 systemd unit

`/etc/systemd/system/modryn.service`:

```ini
[Unit]
Description=MODRYN on Odoo 19
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service

[Service]
Type=simple
User=odoo
Group=odoo
# TZ=UTC everywhere. The 1-minute SOS cron and the 24h reminder sweep both do
# date arithmetic; a host in Asia/Jerusalem makes DST a silent correctness bug.
Environment=TZ=UTC
Environment=LANG=C.UTF-8
# rtlcss is a global npm binary and Odoo shells out to it to build the RTL
# bundle. Without it on PATH the Hebrew storefront renders LTR-ish and nothing
# logs an error.
Environment=PATH=/opt/modryn/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/modryn/.venv/bin/python /opt/modryn/odoo/odoo-bin server -c /etc/odoo/odoo.conf
KillMode=mixed
# Prefork forks 53 children; give the tree time to drain rather than SIGKILL
# mid-request.
TimeoutStopSec=120
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=modryn

# Filesystem containment. data_dir is the only writable path the app needs.
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=/var/lib/modryn

[Install]
WantedBy=multi-user.target
```

`ProtectSystem=strict` will surface anything that writes outside `data_dir`. If a module needs a
temp path, `PrivateTmp` already gives it one; do not widen `ReadWritePaths` to fix a symptom.

---

## 4. nginx

`nginx -t` must pass before every reload, and a reload is part of every deploy.

### 4.1 The catch-all — the hazard this fixes

Measured on the running instance:

```
GET http://unknown-tenant.localtest.me:8069/
  -> 303  http://unknown-tenant.localtest.me:8069/odoo
  -> 303  http://unknown-tenant.localtest.me:8069/web/database/selector
  -> 200
```

An unknown subdomain lands on Odoo's **database selector**, which lists databases and links to the
manager. (`GET /shop` on the same unknown host returns a plain 404 — the hazard is specific to
paths that fall through to Odoo's backend redirect, which includes `/`.)

Two independent defences, because either alone is a single point of failure:

**(a) A `default_server` that never proxies.** Any Host not in `tenants.map` gets a static 404 and
never touches Odoo.

```nginx
server {
    listen 80  default_server;
    listen 443 ssl default_server;
    server_name _;

    ssl_certificate     /etc/letsencrypt/live/modryn.co.il/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/modryn.co.il/privkey.pem;

    # An unknown subdomain must DIE HERE. Reaching Odoo means a 303 to
    # /odoo and then to /web/database/selector — measured, not theoretical.
    # Static file, no proxy_pass: the request never reaches Python.
    root /var/www/modryn-static;
    error_page 404 /404.html;
    location = /404.html { internal; }
    location / { return 404; }
}
```

**(b) `/web/database/` returns 404 on every server block.** See §4.3.

### 4.2 Known tenants

`/etc/nginx/tenants.map`, regenerated by `deploy/gen-tenants-map.sh` from the `db_name` line in
`odoo.conf` — one source of truth, so a tenant cannot exist in nginx but not in the cron
enumerator, or vice versa:

```nginx
# map $host -> 1 for every host this platform serves.
# Generated by deploy/gen-tenants-map.sh — do not hand-edit.
bella.modryn.co.il  1;
noga.modryn.co.il   1;
```

```nginx
map $host $tenant_known {
    default              0;
    include              /etc/nginx/tenants.map;
}
```

The tenant `server` block matches `~^(?<tenant>[a-z][a-z0-9-]{1,30})\.modryn\.co\.il$` — the same
slug regex `new_boutique.sh` enforces — and refuses anything `tenants.map` does not know:

```nginx
server {
    listen 443 ssl http2;
    server_name ~^(?<tenant>[a-z][a-z0-9-]{1,30})\.modryn\.co\.il$;

    # Belt to the default_server's braces: a hostname that matches the slug
    # pattern but has no database still must not reach Odoo, because dbfilter
    # would fail open into the /odoo redirect chain.
    if ($tenant_known = 0) { return 404; }
    ...
}
```

### 4.3 `/web/database/` — defence in depth, and why it is load-bearing

`list_db = False` blocks the *operations* (`odoo/service/db.py:44-51`). It does **not**
unregister the routes. Every one of them is still `auth="none"`, and the mutating ones are
`csrf=False` (`odoo/addons/web/controllers/database.py:59-179`): `create`, `duplicate`, `drop`,
`backup`, `restore`, `change_password`. Their only gate is `master_pwd`.

That is a lot of surface to leave answering, so it does not answer:

```nginx
# Odoo's db manager: auth="none" and csrf=False on create/drop/backup/restore,
# gated only by master_pwd. list_db=False blocks the operations but leaves the
# routes registered. Nothing legitimate on this platform requests these paths.
location ^~ /web/database/ {
    return 404;
}
```

Placed in **both** the tenant block and the `default_server`.

### 4.4 TLS

Wildcard certificate for `*.modryn.co.il` via Let's Encrypt **DNS-01**. HTTP-01 cannot issue a
wildcard, and per-tenant certificates would mean a certificate request in the provisioning path —
turning a 20-second `createdb -T` into something that can be rate-limited by a third party.

**USER-OWNED:** the domain, the DNS provider account, and an API token scoped to
`_acme-challenge` TXT records only.

```bash
certbot certonly --dns-<provider> \
  --dns-<provider>-credentials /etc/letsencrypt/dns.ini \
  -d modryn.co.il -d '*.modryn.co.il'
```

`/etc/letsencrypt/dns.ini` is `0600 root:root`. Renewal is certbot's systemd timer with
`--deploy-hook "systemctl reload nginx"`.

```nginx
ssl_protocols       TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
ssl_session_cache   shared:SSL:50m;
ssl_session_timeout 1d;
ssl_stapling        on;
ssl_stapling_verify on;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

`includeSubDomains` is correct here and also a commitment: every tenant subdomain becomes
HTTPS-only permanently. That is intended.

Port 80 redirects, except the ACME path:

```nginx
server {
    listen 80;
    server_name ~^[a-z][a-z0-9-]{1,30}\.modryn\.co\.il$;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://$host$request_uri; }
}
```

### 4.5 Proxying, websockets and the filestore

```nginx
upstream odoo      { server 127.0.0.1:8069; keepalive 64; }
upstream odoo_chat { server 127.0.0.1:8072; keepalive 32; }

proxy_http_version 1.1;
proxy_set_header Connection "";

# X-Forwarded-Host is NOT optional. Odoo's entire ProxyFix block is gated on
# it (odoo/http.py:2834); without it proxy_mode silently does nothing and every
# request looks like plain HTTP from 127.0.0.1.
proxy_set_header Host              $host;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP         $remote_addr;

location / {
    proxy_pass http://odoo;
    proxy_read_timeout 300s;      # > limit_time_real (240s), so Odoo's own
                                  # limit fires first and logs the culprit
    client_max_body_size 64m;     # dress photography
}

location /websocket {
    proxy_pass http://odoo_chat;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
    # Odoo's websocket_keep_alive_timeout defaults to 3600s
    # (odoo/tools/config.py:217). nginx must outlast it or nginx becomes the
    # thing that drops the floor board's connection, and the reconnect storm
    # looks like an application bug.
    proxy_read_timeout 3700s;
    proxy_send_timeout 3700s;
}

# X-Sendfile target. Odoo emits X-Accel-Redirect: /web/filestore/<db>/<sha[:2]>/<sha>
# built relative to data_dir/filestore (odoo/http.py:669-678), with
# Content-Length: 0 on purpose — without that header nginx waits for a body
# that never arrives. `internal` means only Odoo can trigger it; a direct
# request to /web/filestore/... 404s.
location /web/filestore {
    internal;
    alias /var/lib/modryn/filestore;
    add_header Cache-Control "private, max-age=86400";
}
```

### 4.6 Caching and compression — and the trap

Two measured facts decide this:

1. Odoo already sets `Cache-Control: public, max-age=31536000, immutable` on
   `/web/assets/<id>/<hash>/...`, and the hash is in the path. **Browser caching is already
   correct; nginx does not need to improve it.**
2. Odoo does **not** compress. Requesting `/shop` with `Accept-Encoding: gzip` returns no
   `Content-Encoding`. The frontend CSS bundle is 995,309 bytes uncompressed. **gzip in nginx is
   mandatory, not an optimisation** — it is the difference between a ~1 MB and a ~120 KB cold page
   load, and Israeli mobile users are a real share of this traffic.

```nginx
gzip              on;
gzip_vary         on;
gzip_comp_level   5;
gzip_min_length   1024;
gzip_proxied      any;
gzip_types text/plain text/css text/xml application/javascript
           application/json application/xml image/svg+xml;
```

> **Do not add `proxy_cache` to `/web/assets/` with `proxy_ignore_headers Set-Cookie`.**
> Measured: a `GET` of a CSS bundle came back with
> `Set-Cookie: session_id=...; HttpOnly; Max-Age=604800` alongside the immutable cache header,
> because Odoo touches the session on that request. nginx normally refuses to cache a response
> carrying `Set-Cookie`; `proxy_ignore_headers Set-Cookie` removes that refusal and turns the asset
> cache into a machine that hands one visitor's session cookie to the next. The immutable header
> plus gzip already gets the win with none of the risk.

**Launch gate — self-host the fonts.** The storefront currently preconnects to
`fonts.gstatic.com` and pulls Frank Ruhl Libre and Assistant from the Google CDN. The comment in
`addons/modryn_theme/static/src/scss/primary_variables.scss:16-19` already records this: *"MODRYN
self-hosts these via @fontsource. The PoC uses the Google CDN because it is Odoo's native path;
production parity would mean shipping the woff2 files as addon assets."* In production this is
three problems: a third-party runtime dependency on every cold page load, a privacy exposure
(Google Fonts is a known issue under EU and Israeli privacy law), and a ramp campaign that
measures Google's latency instead of ours. Ship the woff2 files as addon assets before the DNS
flip.

### 4.7 Rate limiting

Zones on the public POST endpoints — the routes that write, send SMS, or create records for an
anonymous caller. Verified `auth='public'` + `methods=['POST']`:

| Route | Addon | Why it needs a limit |
|---|---|---|
| `/book/submit` | `modryn_booking` | Creates a `calendar.event`, sends a confirmation SMS |
| `/queue/checkin/submit` | `modryn_queue_poc` | Creates a queue entry |
| `/waitlist/join` | `modryn_portal` | Creates a waitlist row, sends SMS |
| `/my/login`, `/my/verify` | `modryn_portal` | Phone OTP — SMS cost and credential guessing |
| `/staff/login` | `modryn_staff` | Credential guessing |
| `/claim/<token>` | `modryn_portal` | Consumes a waitlist offer |
| `/b/<token>/confirm`, `/b/<token>/cancel` | `modryn_portal` | Mutates a booking by token |

Anything that sends SMS is a **money** endpoint, not just a compute one.

```nginx
# Keyed on IP. Israeli mobile carriers CGNAT heavily, so these are deliberately
# loose: the goal is to stop a script, not to police a shared exit address.
limit_req_zone $limit_key zone=modryn_post:10m  rate=10r/m;
limit_req_zone $limit_key zone=modryn_otp:10m   rate=5r/m;
limit_req_status 429;

# THE LOAD GENERATOR MUST BE EXEMPT. Without this the ramp campaign measures
# nginx's 429 rate and calls it an Odoo capacity ceiling — a whole afternoon
# spent tuning workers against a limit that lives in this file.
geo $limit_exempt {
    default        0;
    <LOADGEN_IP>/32 1;      # USER-OWNED — the ramp generator's address
    127.0.0.1/32   1;
}
map $limit_exempt $limit_key {
    0 $binary_remote_addr;
    1 "";                   # empty key disables the limit for this request
}

location = /book/submit          { limit_req zone=modryn_post burst=5 nodelay; proxy_pass http://odoo; }
location = /queue/checkin/submit { limit_req zone=modryn_post burst=5 nodelay; proxy_pass http://odoo; }
location = /waitlist/join        { limit_req zone=modryn_post burst=5 nodelay; proxy_pass http://odoo; }
location = /my/login             { limit_req zone=modryn_otp  burst=3 nodelay; proxy_pass http://odoo; }
location = /my/verify            { limit_req zone=modryn_otp  burst=3 nodelay; proxy_pass http://odoo; }
location = /staff/login          { limit_req zone=modryn_otp  burst=3 nodelay; proxy_pass http://odoo; }
```

Remove the load-generator exemption after the campaign; leave `127.0.0.1` so `verify.sh` — which
hammers `/staff/login` in §10a — keeps passing.

### 4.8 robots.txt and error pages

Odoo serves `/robots.txt` itself (`addons/website/controllers/main.py:239`), per website. Leave it
proxied — a static override would break per-tenant control the website module already gives the
owner.

Static, Odoo-independent error pages, so a 502 is a page rather than nginx's default:

```nginx
error_page 404 /404.html;
error_page 500 502 503 504 /50x.html;
location = /404.html { root /var/www/modryn-static; internal; }
location = /50x.html { root /var/www/modryn-static; internal; }
```

Both pages must be RTL Hebrew with the MODRYN palette, and must reference no asset from
`/web/assets/` — that path is exactly what is unavailable when a 502 fires.

---

## 5. Backups

Backups exist to be restored. The only evidence a backup works is a restore.

### 5.1 What is backed up

| What | How | Why not the other way |
|---|---|---|
| Each tenant database | `pg_dump -Fc` per tenant | Custom format restores selectively and in parallel; a per-tenant file means a single boutique can be restored without touching its neighbours — the main operational win of DB-per-tenant |
| `modryn_template` | `pg_dump -Fc` | Losing it does not lose customer data but does lose the ability to provision, and rebuilding it is a multi-minute module install plus the Hebrew/Arabic/ILS configuration in `build_template.sh` |
| `/var/lib/modryn/filestore` | `rsync -a --delete` | Attachments are on disk, not in Postgres. A database dump alone restores rows that point at files that do not exist. |
| `/etc/odoo/odoo.conf`, `/etc/nginx`, `/etc/systemd/system/modryn.service` | `rsync` | Cheap; makes rebuild-from-bare-metal a scripted operation |

`/var/lib/modryn/sessions` is **not** backed up. Restoring stale sessions is worse than making
everyone sign in again.

### 5.2 Schedule and retention

Nightly at **02:30 UTC** — chosen to sit clear of the 1:00–3:00 local DST window and outside
Israeli boutique hours.

| Tier | Kept | Location |
|---|---|---|
| Daily | 7 | local `/var/backups/modryn/daily/` |
| Weekly (Sunday) | 4 | local `/var/backups/modryn/weekly/` |
| Offsite | mirror of both | Hetzner Storage Box over `rsync` + SSH key — **USER-OWNED** (order the box, create the key) |

Offsite is not optional. Seven local dailies on the same RAID1 pair survive a disk failure and not
a filesystem, a fat-fingered `dropdb`, or the machine.

### 5.3 `deploy/backup.sh` — behaviour contract

```
for each db in db_name (from odoo.conf) + modryn_template:
    pg_dump -Fc -f daily/<date>/<db>.dump <db>
rsync -a --delete /var/lib/modryn/filestore/ daily/<date>/filestore/
rsync -a /etc/odoo /etc/nginx /etc/systemd/system/modryn.service daily/<date>/etc/
on Sunday: hardlink daily/<date> -> weekly/<date>
prune daily > 7, weekly > 4
rsync -a --delete /var/backups/modryn/ <offsite>:/modryn/
write a one-line result to /var/log/modryn-backup.status
```

- **Non-zero exit on any failure, and never a silent partial.** A backup script that half-works and
  exits 0 is worse than no backup, because it removes the anxiety that would otherwise catch it.
- **Read the tenant list from `odoo.conf`, never a hard-coded array.** A boutique provisioned on
  Tuesday must be in Tuesday night's backup without anyone remembering to edit a second file.
  `modryn_template` is appended explicitly because §3 deliberately removed it from `db_name`.
- `pg_dump` runs as the `odoo` role over the unix socket. It counts against `max_connections` —
  one connection at a time, well inside the 35-connection headroom from §2.4.

### 5.4 Restore, and the drill

`deploy/restore.sh <tenant> <dump-file>` must:

1. Refuse to overwrite a live database. Restore to `<tenant>_restore`, never in place.
2. `createdb` + `pg_restore -j 4`.
3. `cp -R` the filestore for that tenant from the same backup set.
4. Run the same fixups `new_boutique.sh` runs — **fresh `database.uuid`**, `web.base.url`,
   `web.base.url.freeze` — because a restored copy running alongside the original is exactly the
   "two tenants believing they are the same instance" case `new_boutique.sh` was written to
   prevent.
5. Print the verification command and stop. A human decides whether to promote.

**Launch gate:** one full restore drill — dump, restore to a scratch name, point a hostname at it,
`verify.sh` green against the restored tenant — must pass before the DNS flip. **Monthly
thereafter**, with the date and result recorded in §9. An untested backup is a belief, not a
backup.

---

## 6. Monitoring

**netdata**, bound to `127.0.0.1` and reached over an SSH tunnel — never exposed, never behind a
password we have to manage.

Signals that matter here, in order:

| Signal | Why it is the one that matters |
|---|---|
| Odoo worker count vs. `workers` | Workers recycling constantly means `limit_memory_soft` is being hit; the symptom is latency, the cause is memory |
| `pg_stat_activity` count vs. 250 | §2.4's ceiling is 212. Approaching 250 means connection refusals, not slowness |
| 5xx rate from the nginx access log | The only number a boutique owner would recognise |
| p95 latency on `/shop`, `/book`, `/floor` | One customer path, one booking path, one staff path |
| `nginx 429` count | If this is non-zero outside the ramp, §4.7's limits are too tight for real Israeli CGNAT traffic |
| Cron pass duration | §2.5's ceiling. A pass creeping past 60 s means the SOS escalation is late |
| Backup status file age | A backup that stopped running is silent by nature |

Plus **external uptime checks** — UptimeRobot or equivalent, **USER-OWNED** — hitting
`https://bella.modryn.co.il/shop` and one deliberately unknown subdomain that must return 404.
The second check is the regression test for §4.1, and it is the one that would catch the database
selector coming back after an nginx edit.

**Prometheus and Grafana are deferred.** On one host they are three more services to run, patch and
back up in order to graph a machine that netdata already graphs out of the box. They earn their
keep when there is a second box to correlate against — that is the trigger, and §2.6 is when it
fires.

---

## 7. Security baseline

| Control | Configuration | Reason specific to this system |
|---|---|---|
| Firewall | `ufw default deny incoming`; allow 22, 80, 443 only | Odoo binds loopback (§3), so 8069/8072 must never be reachable — a public bind would bypass §4.3's database-manager block entirely |
| SSH | Keys only: `PasswordAuthentication no`, `PermitRootLogin no` | **USER-OWNED:** the admin public keys |
| Service user | `odoo`, no shell (`/usr/sbin/nologin`), owns `/var/lib/modryn` only | Odoo writes attachments an anonymous visitor can influence; the process must not own its own code |
| PostgreSQL | `listen_addresses = ''` — unix socket only; `peer` auth for `odoo` | With no TCP listener there is no network attack surface and no password to leak. This is also why `db_host` is empty in §3, and why managed Postgres was rejected in §1 |
| Patching | `unattended-upgrades` for security updates; automatic reboot **off** | An unplanned reboot during boutique hours is worse than a patch landing an hour late. Reboots are scheduled |
| `fail2ban` | Jail on Odoo login failures via journald | `/staff/login` and `/my/login` are the credential surfaces; §4.7 rate-limits them, fail2ban bans the persistent |

fail2ban filter — Odoo logs failed logins to journald under `SyslogIdentifier=modryn`:

```ini
# /etc/fail2ban/filter.d/modryn-odoo.conf
[Definition]
failregex = ^.*Login failed for db:\S+ login:\S+ from <HOST>.*$
ignoreregex =
```

```ini
# /etc/fail2ban/jail.d/modryn.conf
[modryn-odoo]
enabled  = true
backend  = systemd
journalmatch = SYSLOG_IDENTIFIER=modryn
maxretry = 6
findtime = 600
bantime  = 3600
```

`<HOST>` is only correct because `proxy_mode = True` **and** nginx sends `X-Forwarded-Host`
(§3, §4.5). Without that header Odoo logs `127.0.0.1` for every failure and the jail bans nginx —
taking the whole platform down on the sixth wrong password.

### Secrets

Twilio credentials are per-tenant `ir.config_parameter` rows, loaded by
`scripts/configure_twilio.py` from a gitignored `.env`. In production:

- `/etc/odoo/twilio.env`, `0600 root:root`, **never** in the repo.
- **USER-OWNED and outstanding:** the credentials currently in `.env` were pasted into a chat
  transcript on 2026-08-10 (`.planning/BACKLOG.md` item 2). **Rotate them in the Twilio console
  before the DNS flip.** A transcript is not a secret store.
- The absence of credentials is safe by construction: `modryn.sms.send()` logs and returns
  `(True, 'logged')` when the four keys are missing (`modryn.sms.send()` in
  `addons/modryn_portal/models/sms.py`), and never raises. A tenant provisioned without credentials silently does not text anyone rather
  than 500ing — deliberate, and worth knowing before someone reads "logged" as "delivered".
- **Launch gate:** SMS delivery to a second handset is still unproven
  (`.planning/STATE.md`, `.planning/BACKLOG.md` item 1). The comms engine is *integrated*, not
  *delivered*. Prove one end-to-end delivery before launch or ship with reminders disabled.

---

## 8. `deploy/` layout

```
deploy/
├── provision.sh              # bare Debian -> serving. Idempotent, re-runnable.
├── deploy.sh                 # ship a new commit
├── gen-tenants-map.sh        # odoo.conf db_name -> /etc/nginx/tenants.map
├── backup.sh                 # §5.3
├── restore.sh                # §5.4
├── etc/
│   ├── odoo.conf             # §3, with secrets as placeholders
│   ├── modryn.service        # §3.1
│   ├── postgresql.conf.d/
│   │   └── modryn.conf       # §2.4 overrides only, never a whole postgresql.conf
│   ├── nginx/
│   │   ├── modryn.conf       # tenant server block
│   │   ├── default-404.conf  # §4.1 catch-all
│   │   └── limits.conf       # §4.7 zones
│   └── fail2ban/             # §7
└── www/
    ├── 404.html              # §4.8 — no /web/assets references
    └── 50x.html
```

| Artifact | Contract |
|---|---|
| `provision.sh` | Bare Debian 12 → serving. Installs system packages, PostgreSQL 16, nginx, Python 3.12, node + `rtlcss`, `cairo`/`pkg-config` for `rlPyCairo`; creates the `odoo` user and `/var/lib/modryn`; installs everything under `etc/`; enables units. **Idempotent — every step skips if already done**, the same discipline `scripts/bootstrap.sh` already follows. This script *is* the reproducibility argument from §1; if it stops being re-runnable, the no-Docker decision stops being defensible. |
| `deploy.sh` | `git fetch && git checkout <sha>` → `pip install -r odoo/requirements.txt` → `odoo-bin server -u all --stop-after-init` **per tenant** → `systemctl restart modryn` → `nginx -t && systemctl reload nginx` → `scripts/verify.sh`. Non-zero exit if `verify.sh` reports any failure. |
| `gen-tenants-map.sh` | Parses the `db_name` line from `odoo.conf` and writes `tenants.map`. Run by `new_boutique.sh` and by `deploy.sh`. One source of truth for "which tenants exist". |
| `postgresql.conf.d/modryn.conf` | Only the §2.4 overrides. Never a wholesale `postgresql.conf`, or the next PostgreSQL point release silently reverts settings that were never ours to own. |

### Two things `deploy.sh` must get right

**Module upgrades are a loop over N databases.** `docs/scorecard.md` row 4 calls this out as the
red mark against DB-per-tenant: `-u all` runs once per tenant, serially, and the platform is down
for the duration. At two tenants this is a minute. At fifty it is the reason the scorecard says a
cross-tenant console becomes a platform team's job. `deploy.sh` must print elapsed time per tenant
so the trend is visible before it becomes a crisis.

**Provisioning must not need downtime.** `new_boutique.sh` aborts when `pg_stat_activity` shows any
connection to `modryn_template`, because `createdb -T` requires zero. Measured on the running dev
instance: **three persistent idle connections to `modryn_template`**, held purely because it is
listed in `db_name`. §3 removes it from the production `db_name` for exactly this reason. Verify
after any config change:

```bash
psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='modryn_template'"
# must print 0 while the server is running
```

If it prints anything else, adding a boutique needs a restart, and `new_boutique.sh` will say so
rather than silently producing a broken tenant.

---

## 9. Tuning log

To be filled during the ramp campaign. One row per change, with the measurement that justified it.
A row without a "measured" column is an opinion.

| Date | Setting | From | To | Measured before | Measured after | Why |
|---|---|---|---|---|---|---|
| _example_ 2026-08-14 | `workers` | 48 | 40 | 400 req/s, p95 1,850 ms, CPU 98 %, 6 workers recycling/min on `limit_memory_soft` | 400 req/s, p95 620 ms, CPU 91 %, 0 recycles | 48 workers oversubscribed 32 threads badly enough that context switching cost more than the extra concurrency bought. Memory recycling was the tell: 48 × ~340 MB measured (not the 300 MB planned) exceeded what was left after `shared_buffers`. |
| | | | | | | |
| | | | | | | |
| | | | | | | |

### Restore drill log

| Date | Tenant | Dump age | Restore time | `verify.sh` result | Notes |
|---|---|---|---|---|---|
| | | | | | |

### What the ramp campaign must measure

1. **Mean and p95 service time `S`** for `/shop`, `/book`, `/book/submit`, `/floor`. §2.2's worker
   count is a function of `S`; everything else follows.
2. **Worker RSS under load.** The 300 MB in §2.2 is planned, not measured. Replace it.
3. **Peak `pg_stat_activity`** against the 212 ceiling and the 250 limit.
4. **Full cron pass duration** against the 1-minute SOS interval (§2.5).
5. **nginx 429 count — must be zero** during the ramp. Non-zero means §4.7's load-generator
   exemption is not working and every capacity number from that run is invalid.

---

## 10. USER-OWNED items

Nothing in this list can be done from the repository. Everything else in this spec can.

| # | Item | Blocks |
|---|---|---|
| 1 | Hetzner account + AX102-class box order | Everything |
| 2 | Domain registration and DNS control for `modryn.co.il` (placeholder — the real domain is a decision) | §4 |
| 3 | DNS provider API token scoped to `_acme-challenge` TXT records | §4.4 wildcard TLS |
| 4 | Wildcard `A`/`AAAA` record `*.modryn.co.il` → box IP | §4 |
| 5 | Generate and store the `admin_passwd` plaintext in a password manager | §3 |
| 6 | Admin SSH public keys | §7 |
| 7 | Hetzner Storage Box (or equivalent) for offsite backups | §5.2 |
| 8 | External uptime monitoring account | §6 |
| 9 | Load-generator source IP, for the §4.7 exemption | The ramp campaign |
| 10 | **Rotate the Twilio credentials** exposed in the 2026-08-10 transcript | §7 — before the flip |
| 11 | A destination mobile number to prove one SMS delivery end to end | §7 launch gate |

---

## 11. Launch gates

The DNS flip does not happen until every line is green.

| # | Gate | Evidence |
|---|---|---|
| 1 | `scripts/verify.sh` — 85 passed, 0 failed, against the production box | Script exit code 0 |
| 2 | Unknown subdomain returns a static 404 and never reaches Odoo | `curl -I https://nope.modryn.co.il/` → 404, zero lines in the Odoo journal |
| 3 | `/web/database/manager` and `/web/database/selector` return 404 on every host | `curl -I` both, on a tenant host and on the catch-all |
| 4 | `pg_stat_activity` shows **0** connections to `modryn_template` while serving | §8 one-liner |
| 5 | One full restore drill passed | §5.4, logged in §9 |
| 6 | Fonts self-hosted; no request to `fonts.gstatic.com` on any page | Browser network tab on `/shop` |
| 7 | One SMS delivered to a real second handset | §7 launch gate, item 11 above |
| 8 | Twilio credentials rotated | §10 item 10 |
| 9 | Ramp campaign complete, tuning log §9 has at least the five measurements listed | §9 |
| 10 | nginx 429 count zero during the final ramp run | §9 item 5 |

---

## Appendix A — corrections found while verifying this spec

Recorded because each one would have produced a real production failure, and because the next
person deserves to know these were checked rather than assumed.

| # | Assumption | What the code says |
|---|---|---|
| 1 | An unknown subdomain redirects to the database **manager** | It 303s to `/odoo`, then to **`/web/database/selector`**. Same `/web/database/` prefix, so the §4.3 block covers both — but `/shop` on an unknown host already 404s, so the hazard is narrower than "any request". |
| 2 | Connection ceiling is `workers × db_maxconn` = 192 | `db_maxconn` is **per process** (`odoo/sql_db.py:621`), and prefork runs cron workers as processes plus one gevent process. Real ceiling **212**. |
| 3 | (unstated) One connection pool per process | True only while `db_replica_host` is unset. Setting it creates a second pool per process (`odoo/orm/registry.py:254-257`) and doubles the ceiling to 424. |
| 4 | Keep `modryn_template` in the `db_name` list | `db_name` must stay — `cron_database_list()` is `config['db_name'] or list_dbs(True)` (`server.py:99-100`), exactly as the existing comment claims. But the **template must be removed from it**: measured, the server holds 3 persistent connections to it, and `new_boutique.sh` aborts on any connection to the template. Keeping it means provisioning a boutique requires downtime. |
| 5 | The gevent process is a second service to run | `PreforkServer` spawns it automatically (`server.py:944`) when `workers > 0`. A second systemd unit would double-bind port 8072. |
| 6 | `limit_memory_soft` is in megabytes | **Bytes** (`odoo/tools/config.py:465,475`). `= 2048` means 2 KB and kills every worker at startup. |
| 7 | `proxy_mode = True` is sufficient | It is gated on `X-Forwarded-Host` being present (`odoo/http.py:2834`). Without that nginx header it silently does nothing — and §7's fail2ban jail then bans nginx. |
| 8 | Asset caching should be an nginx `proxy_cache` | Odoo already sets `immutable, max-age=31536000` (measured), **and** can attach `Set-Cookie: session_id` to the same response. `proxy_ignore_headers Set-Cookie` would leak sessions between visitors. gzip is the real win; Odoo does not compress at all (measured). |
| 9 | `list_db = False` removes the database-manager routes | It blocks the operations only (`odoo/service/db.py:44-51`). The routes stay registered at `auth="none"`, with `csrf=False` on create/drop/backup/restore (`addons/web/controllers/database.py:59-179`). §4.3 is load-bearing, not ceremony. |
| 10 | Websockets scale with users | Only two bus subscribers exist, both staff-only. No customer surface holds a websocket, so concurrency is staff × tenants — ~200, not 10,000. |
| 11 | A second app box needs cron disabled for correctness | `ir_cron` claims jobs with `FOR NO KEY UPDATE SKIP LOCKED` (`ir_cron.py:330,365`), safe across processes and hosts. Disable it on the second box to avoid wasted polling, not to avoid double-firing. |
