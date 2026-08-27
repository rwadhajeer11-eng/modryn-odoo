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
| http://bella.localtest.me:8069/odoo | staff back office (`admin`, password you set at setup) |

Working: subdomain tenancy · Hebrew-first RTL · Arabic toggle · luxury theme from
MODRYN's tokens · dress catalog with per-size stock · price-visibility toggle ·
dual-path booking (dress-bound + standalone) with server-side terms enforcement ·
QR walk-in check-in · live queue board over websockets.

Also working: phone OTP login · SMS through Twilio (queued in an outbox, drained by cron)
· day waitlist with one standing offer at a time · staff floor board · roster · alterations.

Deliberately **not** built — each is a Phase-2 line item in the scorecard: availability
engine, Israeli payment gateway, WhatsApp.

## Layout

```
odoo/          Odoo 19 source — shallow clone, gitignored, NEVER edited
addons/        our seven addons (the only customization surface)
  modryn_theme/       palette, fonts, RTL, price-visibility toggle
  modryn_booking/     dual-path booking on calendar.event
  modryn_queue_poc/   QR check-in + bus.bus live board
  modryn_portal/      phone OTP login, her bookings, SMS outbox, day waitlist
  modryn_staff/       staff login + floor board
  modryn_atelier/     alterations
  modryn_roster/      shifts, availability, weekly rota
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
./scripts/build_template.sh                 # golden DB: core + all seven modryn addons,
                                            # he_IL + ar, ILS, variants
./scripts/new_boutique.sh bella "Bella Bridal"
./scripts/new_boutique.sh noga  "Noga Couture"

source .venv/bin/activate
MODRYN_SLUG=bella ./odoo/odoo-bin shell -c odoo.conf -d bella --db-filter='^bella$' --no-http < scripts/seed_catalog.py
MODRYN_SLUG=noga  ./odoo/odoo-bin shell -c odoo.conf -d noga  --db-filter='^noga$'  --no-http < scripts/seed_catalog.py

./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1     # run it
```

**The addons are installed once, into the template.** A boutique is `createdb -T`
plus the fixups — no module install per tenant, which is why provisioning is seconds
rather than minutes. It also means the template is the only place the launch-critical
unique indexes can come from, so both scripts assert those indexes exist and refuse to
hand you a tenant without them. If `new_boutique.sh` tells you the template is missing
one, `dropdb modryn_template` and rebuild it; every existing tenant is unaffected.

No credential is stored in this repo, so you pick your own. Set the back-office `admin`
password right after `build_template.sh` (Settings → Users → Administrator), and export
`MODRYN_DEMO_PASSWORD` before running `scripts/seed_staff.py` — that seeder has no default
password and exits if the variable is unset.

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

## Traps worth knowing before writing an addon

Every one of these failed **silently** — no error, no log line — which is the point.

1. **`'category': 'Theme/*'` disables all your assets.** `website/models/ir_asset.py`
   discards the assets of every module in a Theme category except the website's selected
   `theme_id`. Our theme's SCSS vanished until the category changed to `Website`.
2. **Odoo compiles SCSS with LibSass.** Modern CSS Color Level 4 syntax
   (`rgb(43 33 24 / 0.1)`) fails with *"Function rgb is missing argument $green"* and
   takes the **entire frontend bundle** down, not just that rule. Use `rgba()`.
3. **`recordset.mapped(callable)` on an EMPTY recordset** calls the callable once with the
   recordset itself, so `empty.start` is `False`. Use a comprehension.
4. **`_sql_constraints` no longer exists in Odoo 19.** A model still declaring it gets no
   index and no warning — the constraint simply is not there. Use
   `_name_uniq = models.Constraint('unique(name)', "…")`. Caught only because a duplicate
   role sailed through a form that was supposed to reject it.
5. **A `translate=True` field is stored as `jsonb`**, so `unique(name)` compares whole JSON
   objects: `{"en_US": "x"}` and `{"en_US": "x", "he_IL": "x"}` are "different" and a
   visible duplicate gets through. Worse, flipping the field to non-translatable does
   **not** migrate the column, and the resulting mismatch makes every write fail with
   `InvalidTextRepresentation`. Enforce that kind of uniqueness in Python.
6. **CSRF depends on a session cookie that a plain GET does not create.** The token is an
   HMAC over `session.sid`, but Odoo only sends the cookie when the session is *dirty*. A
   visitor whose **first** request is your form posts under a new sid and gets a bare 400.
   Call `request.session.touch()` when rendering a form that anonymous users land on
   directly. Other pages hide this bug because the visitor already had a cookie.

7. **A write to a non-stored computed field is silently thrown away.** No error, no
   warning, `write()` even returns `True` — the value simply never lands. This is how
   turning `hr_employee.modryn_role_id` into a compute over the new many-to-many nearly
   shipped a boutique where every woman had no role at all: two seeding scripts still
   wrote the old field name, `seed_staff.py` would have "succeeded", and
   `modryn_can_view` falls straight to `if not roles: return False` — so the whole team
   would have been locked out of every page but her own home, with nothing in any log
   to say why. **Searching** one raises loudly (`Cannot convert … to SQL because it is
   not stored`); writing one does not. Give any compute that keeps its old name an
   `inverse=`, and grep the repo for every writer before you make the change.

Also renamed in 19: `res.users.groups_id` → `group_ids`, and `res.groups.category_id` →
`privilege_id` (pointing at a new `res.groups.privilege` model). That drift is the real
cost of a version upgrade, and it is why the addon count matters.

---

## Deployment

Everything needed to run this in production lives in **[`deploy/`](deploy/)**, and the runbook
is **[`deploy/README.md`](deploy/README.md)** — provision, deploy, add a boutique, rotate
certificates, back up, restore, roll back.

| | |
|---|---|
| `deploy/provision.sh` | idempotent Ubuntu 24.04 bootstrap; re-run it to apply a config change |
| `deploy/odoo.conf.prod` | production Odoo config, every directive carrying its reason |
| `deploy/nginx/` | catch-all 404, `/web/database/` 404, websocket proxy, rate limits, X-Accel filestore |
| `deploy/postgresql/tuning.conf` | PostgreSQL 16 overrides + `pg_stat_statements` |
| `deploy/systemd/` | the service unit and the nightly backup timer |
| `deploy/scripts/` | `deploy.sh`, `new_boutique_prod.sh`, `build_template_prod.sh`, `backup.sh`, `restore.sh` |

The reasoning behind the sizing and the hosting choice is in
`.planning/specs/deployment-spec.md`; where these artifacts deliberately depart from it, the
correction is listed at the end of `deploy/README.md`.
