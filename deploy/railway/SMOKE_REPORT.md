# Railway demo — deployment & smoke report (2026-08-14)

**Live URL:** https://odoo-demo-production-1e73.up.railway.app
**Tenant:** `te` ("TE") — fresh, deliberately empty of business data; 3 seeded staff logins.
**Why this exists:** temporary public demo while `modryn.co.il` is stuck at DomainTheNet. The real production design stays `deploy/` (VPS + nginx).

Credentials (owner `tal`, manager `eden`, staff `gal`, the rotated demo password, the Odoo master password, and the `odoo` Postgres role password) are NOT in this file — they were handed over in the session that produced this report; keep them in a password manager.

## What is deployed

- Railway project `boutique-platform`, **new service `odoo-demo`** (the pre-existing `api`/`worker` services run an unrelated earlier app and were not touched; the pending `*.modryn.co.il` wildcard stays on `api`).
- Image: `deploy/railway/Dockerfile` — python:3.12-slim-bookworm, Odoo 19 cloned at the repo's vendored SHA `6c8e7dc`, rtlcss (Hebrew RTL bundles), no wkhtmltopdf. Seed filestore + gzipped SQL dump baked in.
- Runtime: threaded mode (`workers = 0`, websocket on the single $PORT), `db_name = te` + `dbfilter = ^te$` (any Host → te via monodb), `list_db = False`, volume at `/data` (filestore + sessions), non-superuser Postgres role `odoo` (Odoo refuses to run as `postgres`), healthcheck `/web/health` with a 600 s window (2 GB image pull eats most of it).
- Restore path: `railway ssh -- /app/deploy/railway/bootstrap_restore.sh <url> [force]` — runs inside the container (the Postgres TCP proxy is not public), then `railway redeploy --yes`.

## Smoke results — `scripts/smoke_remote.sh` (pure HTTP, 35 checks)

**33 PASS / 0 FAIL / 2 SKIP**

| § | Checks | Result |
|---|---|---|
| 0 health & routing | health, DB probe, websocket, monodb routing, http→https 301, redirect scheme | 6/6 PASS |
| 1 storefront | /shop, /book (+csrf), /queue/checkin, /my/login, RTL, /en | 7/7 PASS, 2 SKIP (catalog empty by design) |
| 2 assets & filestore | committed font, asset bundle, RTL bundle variant, /web/image | 4/4 PASS |
| 3 security posture | db pages leak no names, db create refused, robots, 404, anonymous /floor gated, anonymous JSON-RPC refused | 7/7 PASS |
| 4 staff (authenticated) | login, /floor, /atelier, /staff/home, /en/floor, /floor/data board + arrivals gate | 7/7 PASS |
| 5 base-url | page carries its host, garbage booking token → 404 | 2/2 PASS |

## QA Playwright (read-only remote subset, `--project=prod`)

**13 passed / 1 failed (verified false alarm)** of the 14 psql-free tests (the other 7 of 21 need local DB access and are out of scope remotely).

- The one failure — act 5 "floor board paints" — asserts >120 chars of rendered text immediately after load; the empty tenant + Railway latency gave 86. A direct re-check with a 6 s settle: **no JS pageerrors, 373 chars painted** (nav, empty-queue panel, today-count). Latency artifact on an empty tenant, not a product bug.

## Explicitly not covered (structural, said plainly)

- `scripts/verify.sh` (330 checks) — cannot run against a PaaS: ~85 peer-auth psql call sites + hardcoded bella/noga subdomain contract.
- k6 loadtest — needs on-box seeding and a loadtest addon that must never ship to a public instance.
- PDF reports — no wkhtmltopdf in the image (demo scope).
- Multi-tenant routing — one hostname, one tenant, by design until the real domain lands.

## Accepted risks (temporary demo)

- No nginx layer: no rate limits on /staff/login and OTP endpoints, no fail2ban, Odoo serves its own static files. `list_db = False` still blocks DB ops; the db pages leak no names (verified).
- Known-format demo logins on a public URL — mitigated by the rotated password.
- Single container, stop-before-start deploys (volume single-attach) — brief downtime on every deploy.
- The `te` SMS layer has no Twilio creds (verified deleted): messages log instead of sending.

## Deploy gotchas now encoded in the files (the short version of a long afternoon)

1. `railway up` honors `.git/info/exclude` (and .gitignore) even with `--no-gitignore` — this repo excludes `/odoo`, `/.odoo-data` there, which silently emptied the upload. 2. A full 1.2 GB context 413s at Railway's edge — the Dockerfile clones Odoo at the pinned SHA instead. 3. `-Fc` dumps couple pg_dump/pg_restore versions — the seed is plain SQL (gzipped). 4. Odoo aborts on `db_user = postgres` — dedicated `odoo` role, CREATEDB, no superuser. 5. A present-but-empty tenant DB 500s every request including /web/health — drop the shell before redeploying. 6. Healthcheck window must absorb the image pull (600 s).

## When modryn.co.il unblocks (flip-back plan)

1. DNS at the registrar (or Hetzner DNS) per the pending Railway records; the wildcard `*.modryn.co.il` is already attached to a service.
2. Move the wildcard domain to `odoo-demo` (or point the app deploy at `api` once that service is retired).
3. Config flip in `deploy/railway/odoo.conf.railway`: `dbfilter = ^%d$`, list real tenants in `db_name`, restore per-tenant `web.base.url`.
4. Real tenants get restored the same way `te` was (`bootstrap_restore.sh` generalizes: dump per tenant, bake or transfer, restore, fixups).
