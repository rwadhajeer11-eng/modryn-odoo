# Plan: launch-readiness tooling

Implementation plan for [`../epics/launch-readiness-tooling.md`](../epics/launch-readiness-tooling.md).
Five features, built in dependency order, each verified before the next begins.

## Order, and why

**E1 → E2 → E3 → E4 → E5.**

E1 is nine lines and unblocks gate 1 today; it also changes `deploy.sh`, so it must be settled
before anything else touches the deploy path. E2 depends on nothing but repairs the three vacuous
gates before anyone relies on them. E3 is self-contained but must land before E5 act 7 can pass.
E4 must land before the load campaign, which must itself run before the first real boutique. E5 is
last because no gate blocks on it.

## Standing constraints for this session

- **One Odoo server, port 8069, shared `bella`/`noga`.** Anything needing a live server — `verify.sh`,
  `-u <module>`, `odoo-bin shell` — is strictly serial. No parallel agent may restart the server or
  run an upgrade.
- **Check what the server actually loads before trusting any run:**
  `ps -ww -o command= -p $(lsof -ti:8069)`. A `--addons-path` override pointing into `.worktrees/`
  means edits to `addons/` do nothing.
- **Reload tiers:** XML data/views → `-u <module> --stop-after-init`; new controller routes or new
  Python files → **full restart**; static OWL/SCSS assets → restart.
- `MODRYN_DEMO_PASSWORD=modryn2026` must be exported or `verify.sh` §10a fails and skips every
  authenticated check.

## E1 — `BASE_HOST`

| # | Task | File |
|---|---|---|
| 1.1 | `cd` to repo root immediately after `set -uo pipefail` | `scripts/verify.sh:8` |
| 1.2 | Replace the `BASE_PORT` line with the scheme/host/port block and `turl()`; add the no-`-k` rationale | `scripts/verify.sh:10-12` |
| 1.3 | `ODOO_CONF` variable; substitute at both read sites | `scripts/verify.sh:95, 957` |
| 1.4 | Production invocation in the §0 failure hint | `scripts/verify.sh:74` |
| 1.5 | `turl "$db"` in §24 | `scripts/verify.sh:~1611` |
| 1.6 | The bella/noga membership gate, exiting 1 | `scripts/verify.sh` after 104 |
| 1.7 | Pass the four variables through to `verify.sh` | `deploy/scripts/deploy.sh:104` |
| 1.8 | Delete the `/etc/hosts` workaround; note the on-box contract | `deploy/README.md` §1e, §11 gate 1 |

**Verify:** baseline count unchanged; `BASE_HOST=nonexistent.invalid` exits 1; `ODOO_CONF=/dev/null`
exits 1; `turl` emits no `:443` under https; zero `-k` in the file.

## E2 — `verify_edge.sh`

| # | Task |
|---|---|
| 2.1 | Header: usage, the "safe against a live box" statement, the two non-read-only checks named |
| 2.2 | `ok`/`bad`/`skip`/`note`/`head_` in the house style; `--remote-only`; `DOMAIN` from `deploy.env`/argv; `ON_BOX` detection |
| 2.3 | Tenant derivation from `db_name` on-box, `TENANT=` required remotely; abort if `$T/shop` is not 200 |
| 2.4 | E1 — 404, zero redirects, `ua_status=-`, **plus the numeric-`ua_status` control** |
| 2.5 | E2 — database manager, 2 hosts × 5 path shapes |
| 2.6 | E3 — prefixed-route positive control (400), **placed before E4** |
| 2.7 | E4 — 12-POST burst, bare and prefixed |
| 2.8 | E5–E8, E12 — 8069, template connections, gzip, filestore, :80 redirect + ACME |
| 2.9 | E9, E10 — chain verify, `-checkend 30d`, HSTS, nosniff |
| 2.10 | E11 — `fail2ban-regex` `1 matched` **and** the captured-host check |
| 2.11 | Runbook: new §8b, gate table rows 2/3/3b/3c/4 point here, gate 10 restated |

**Verify:** `bash -n` clean; `--remote-only` gives 8 pass / 4 skip-with-reason; E3 precedes E4 in
output order.

## E3 — self-hosted type

| # | Task | File |
|---|---|---|
| 3.1 | `fetch_fonts.sh`, run once, six woff2 committed + `OFL.txt` | `scripts/`, `addons/modryn_theme/static/src/fonts/` |
| 3.2 | Six `@font-face` blocks as a new section 0 | `modryn_theme/static/src/scss/modryn.scss` |
| 3.3 | `map-merge` → bare assignment, no `'url'` | `modryn_theme/static/src/scss/primary_variables.scss:46-58` |
| 3.4 | Preconnect removal view + manifest `data` entry | `modryn_theme/views/website_templates.xml`, `__manifest__.py` |
| 3.5 | `-u modryn_theme` on all three databases, then **full restart** (SCSS is a static asset) | — |

**Verify:** the four-path probe reads `0 0 >=2 >0` on every row; woff2 200; `verify.sh` §2 still
passes; the xpath did not silently no-op.

## E4 — k6 through nginx

| # | Task | File |
|---|---|---|
| 4.1 | `origin` in the manifest writer; `BASE_SCHEME`/`BASE_DOMAIN`/`ODOO_CONF`/`NEW_BOUTIQUE`/`FILESTORE` env | `loadtest/seed/gen_tenants.sh` |
| 4.2 | `loadTenantFault()` recomputes `<slug>.<origin>` and compares for equality; `origin` from file only | `loadtest/k6/lib/session.js` |
| 4.3 | `/loadtest/ping` — three gates, same as `read_code` | `loadtest/odoo_addons/modryn_loadtest/controllers/otp.py` |
| 4.4 | `setup()` probes **every** tenant before any VU runs | `loadtest/k6/main.js` |
| 4.5 | Self-check cases for the four accept/reject shapes | `loadtest/k6/lib/session.check.mjs` |
| 4.6 | Sequencing + teardown proof | `loadtest/README.md` |

**Verify:** `node session.check.mjs` passes all cases including the legacy dev manifest;
`/loadtest/ping` 404s identically without the secret.

## E5 — browser QA

| # | Task |
|---|---|
| 5.1 | `qa/package.json`, `qa/playwright.config.js` (`workers: 1`, he-IL, dev/prod projects, `@writes` grepInvert) |
| 5.2 | `.gitignore`: `qa/node_modules/`, `test-results/`, `playwright-report/`, `.auth/` |
| 5.3 | `qa/lib/otp.js` — HMAC preimage over 10⁶, timestamp-derived phone |
| 5.4 | `qa/lib/guard.js` — `globalSetup`, fails closed on missing `QA_TENANTS` |
| 5.5 | Acts 1, 2, 7 — storefront, Arabic, fonts (no writes) |
| 5.6 | Acts 3, 4 — booking, OTP portal (`@writes`) |
| 5.7 | Acts 5, 6 — staff, live websocket board |

**Verify:** 7 specs pass on dev; act 7 fails if E3 is reverted; two runs inside an hour both pass.

## Done

`./scripts/verify.sh` ≥326/0; `verify_edge.sh --remote-only` 8/4-skip; the font probe all-zero;
`session.check.mjs` green; `qa` 7/7. Then update `.planning/STATE.md` and commit per feature.
