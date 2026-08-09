# MODRYN on Odoo — evaluation PoC

A **separate** proof-of-concept that rebuilds MODRYN's concepts on **Odoo 19 Community**,
to answer one question with evidence: *should the bridal-boutique platform be built on
Odoo instead of the custom FastAPI/React stack?*

This repo does not touch the MODRYN codebase and never will. It is an evaluation,
not a migration. The verdict lives in [docs/scorecard.md](docs/scorecard.md).

## What runs today

Two fake boutiques, each a full tenant on its own subdomain:

| | |
|---|---|
| http://bella.localtest.me:8069 | Bella Bridal — 3 dresses |
| http://noga.localtest.me:8069 | Noga Couture — 2 dresses |
| http://bella.localtest.me:8069/odoo | staff back office (`admin` / `modrynpoc123`) |

Working: subdomain tenancy · Hebrew-first RTL · Arabic toggle · luxury theme from
MODRYN's tokens · dress catalog with per-size stock · price-visibility toggle ·
dual-path booking (dress-bound + standalone) with server-side terms enforcement ·
QR walk-in check-in · live queue board over websockets.

Deliberately **not** built — each is a Phase-2 line item in the scorecard: availability
engine, phone OTP, Israeli payment gateway, SMS/WhatsApp, waitlist, roster, alterations.

## Layout

```
odoo/          Odoo 19 source — shallow clone, gitignored, NEVER edited
addons/        our three addons (the only customization surface)
  modryn_theme/       palette, fonts, RTL, price-visibility toggle
  modryn_booking/     dual-path booking on calendar.event
  modryn_queue_poc/   QR check-in + bus.bus live board
scripts/       bootstrap, template build, tenant provisioning, catalog seed
docs/          scorecard + screenshots
odoo.conf      dbfilter tenancy config
```

**The Odoo customization norm:** core is never edited. All behaviour is added through
addons via model inheritance (`_inherit`), view inheritance (XPath on QWeb/XML) and
config. That is what makes a version upgrade survivable — core is replaced wholesale
each release, and your delta stays in `addons/`.

## Setup from scratch

```bash
./scripts/bootstrap.sh                      # clone Odoo, venv, deps, rtlcss
./scripts/build_template.sh                 # golden DB: modules, he_IL + ar, ILS, variants
./scripts/new_boutique.sh bella "Bella Bridal"
./scripts/new_boutique.sh noga  "Noga Couture"

source .venv/bin/activate
MODRYN_SLUG=bella ./odoo/odoo-bin shell -c odoo.conf -d bella --db-filter='^bella$' --no-http < scripts/seed_catalog.py
MODRYN_SLUG=noga  ./odoo/odoo-bin shell -c odoo.conf -d noga  --db-filter='^noga$'  --no-http < scripts/seed_catalog.py

./odoo/odoo-bin server -c odoo.conf -d bella --db-filter='^bella$' -i modryn_theme,modryn_booking,modryn_queue_poc --stop-after-init
./odoo/odoo-bin server -c odoo.conf -d noga  --db-filter='^noga$'  -i modryn_theme,modryn_booking,modryn_queue_poc --stop-after-init

./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1     # run it
```

### How tenancy works

`dbfilter = ^%d$` in `odoo.conf` maps the first hostname label to a database name, so
`bella.localtest.me` serves database `bella`. **One PostgreSQL database per boutique** —
isolation by construction rather than by row-level security. `*.localtest.me` resolves to
127.0.0.1, so wildcard subdomains work locally with no `/etc/hosts` edits.

Provisioning is `createdb -T modryn_template` plus the fixups in `new_boutique.sh`
(fresh `database.uuid`, `web.base.url`, company + website name). Those fixups are not
optional — a raw `createdb -T` leaves two tenants believing they are the same instance.

## Environment notes (macOS, no Docker)

These cost real time; they are recorded so the next person skips them.

- **Use `python3`, never `python`.** A shell alias points `python` at `/usr/bin/python3`
  and silently shadows the venv.
- **`psycopg2` needs `pg_config`** — `export PATH="$(brew --prefix postgresql@16)/bin:$PATH"`.
- **`rtlcss` is required** (`npm i -g rtlcss`). Odoo generates RTL stylesheets by running
  rtlcss over compiled LTR CSS; without it a Hebrew site renders LTR-ish.
- **QR codes need `rlPyCairo`**, which is *not* in `odoo/requirements.txt`. It needs
  `pkg-config` + `cairo` (`brew install pkg-config cairo`), then `pip install rlPyCairo`.
- **`db_host` is a string in Odoo 19** — leave it empty for the unix socket. `False`
  logs a warning and is ignored.
- `--without-demo` is a BOOL in Odoo 19, not the old `all`.
- Odoo 19 split the CLI into subcommands: `odoo-bin server ...`, `odoo-bin shell ...`.

## Three traps worth knowing before writing an addon

1. **`'category': 'Theme/*'` silently disables your assets.** `website/models/ir_asset.py`
   discards the assets of every module in a Theme category except the website's selected
   `theme_id` — no error, no log line. Our theme's SCSS vanished until the category
   changed to `Website`.
2. **Odoo compiles SCSS with LibSass.** Modern CSS Color Level 4 syntax
   (`rgb(43 33 24 / 0.1)`) fails with *"Function rgb is missing argument $green"* and
   takes the **entire frontend bundle** down, not just that rule. Use `rgba()`.
3. **`recordset.mapped(callable)` on an EMPTY recordset** calls the callable once with the
   recordset itself, so `empty.start` is `False`. Use a comprehension.

Also: `res.users.groups_id` was renamed `group_ids` in 19 — the sort of drift that makes
custom addons the real cost of a version upgrade.
