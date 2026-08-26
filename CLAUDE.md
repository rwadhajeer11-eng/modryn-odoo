# CLAUDE.md — read this before touching anything

You are helping a developer who is **completely new to Claude Code** and new to this
codebase. Your job is equal parts builder and patient guide. These rules override
your defaults, and where they conflict with the older `.claude/CLAUDE.md`, **this
file wins**.

## The right rulebook

This is an **Odoo 19 Community / Python** project — a bridal-boutique platform PoC.
One PostgreSQL database per boutique, Hebrew-first RTL with an Arabic toggle.

The older `.claude/CLAUDE.md` and the files in `.claude/rules/` were written for a
different stack — Kotlin, Micronaut, Next.js, React, Terraform — and carry rules
that are **wrong here**: "no foreign keys", "TEXT not VARCHAR", UUID primary keys,
a Java Railway snippet, Kotlin null-safety. Odoo's ORM creates foreign keys and
varchar columns itself; that is correct. Ignore every stack-specific rule from
those files. Also ignore their mentions of Superpowers, Codex and Gemini — tools
on Mrwen's machine, not needed here. What still applies from them: the Spartan
workflow commands, the quality gates, and "match the user's language".

This repo is Python, the Odoo ORM, OWL/QWeb templates, Postgres and nginx. Core
Odoo under `odoo/` is never edited — all behaviour lives in `addons/` via
inheritance. Portal pages follow the existing QWeb templates; no design doc is
needed for small screen changes.

## How to talk to your human

- **Think before acting on every prompt.** Ask yourself: what do they actually
  mean? How does this change affect the rest of the project? Is there a better
  version of what they asked for?
- **Ask when something is unclear or risky** — an ambiguous request, a change
  touching more than a few files, or anything near data, SMS, deploys, or money.
  One question at a time, with your recommended answer marked.
- **Explain everything like they're 10 years old** — questions, plans, summaries,
  errors, all of it. Short sentences. If you must use a technical word, explain it
  in one line ("a migration — a small script that changes the database's shape").
- **Reply in whatever language they write in.**
- For tiny, obvious fixes: skip the question. Lead with one line — "Here's what I
  understood: …" — then do it.

## First-day setup

The Spartan toolkit ships **inside this repo** (`.claude/commands/spartan/`), so
`/spartan:` commands work from a fresh clone — nothing to install. If the project
has no rules config (`.spartan/config.yaml` missing), run `/spartan:init-rules`.

**On this machine the app is already installed and runs inside WSL2**, not on
Windows. Windows has no Python 3.12, no PostgreSQL, and this repo's whole
toolchain is Linux bash talking to Postgres over a unix socket with peer auth —
`verify.sh` alone has ~100 such call sites. Native Windows would mean forking it.

| Piece | Where it actually lives |
|---|---|
| Distro | Ubuntu 24.04 in WSL2 (ships Python 3.12 + PostgreSQL 16 — exactly what Odoo 19 and prod want) |
| Odoo source | `~/modryn-runtime/odoo` on WSL's fast ext4, pinned to SHA `6c8e7dc` |
| venv | `~/modryn-runtime/venv` |
| Odoo data dir | `~/modryn-runtime/data` |
| The repo | stays on Windows at `C:\Users\rwadh\MODRYN\modryn-odoo` — **one copy only** |

`odoo/`, `.venv/` and `.odoo-data/` inside the repo are **symlinks** to those
ext4 directories, so every repo script (`./odoo/odoo-bin`, `source
.venv/bin/activate`, `$REPO/.odoo-data/filestore`) works verbatim, exactly as on
Mrwen's Mac. They are gitignored; the three bare names are in `.git/info/exclude`
because `.gitignore`'s trailing-slash patterns do not match symlinks.

**Running anything:**

```bash
wsl.exe -- bash -c 'cd /mnt/c/Users/rwadh/MODRYN/modryn-odoo && source .venv/bin/activate && ...'
./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1   # start the server
```

From Git Bash, prefix `wsl.exe` with `MSYS_NO_PATHCONV=1` when passing a
`/mnt/c/...` path as an argument, or MSYS rewrites it into a Windows path and the
command fails with "No such file or directory". Multi-line or heavily quoted work
belongs in a script file — quoting through Git Bash → wsl → bash mangles reliably.

`odoo.conf` is tracked but carries this machine's paths and `db_user`. It is
marked `git update-index --skip-worktree`, so the local edit never reaches
Mrwen. If you ever need to see it as tracked again, `--no-skip-worktree`.

**Tenants on this machine:**

| Tenant | Use |
|---|---|
| http://bella.localtest.me:8069 | demo boutique — `verify.sh` asserts its seeded state |
| http://noga.localtest.me:8069 | demo boutique — same |
| http://qa.localtest.me:8069 | **the playground and the browser-test target** |

**Explore on `qa`, never on bella or noga.** Both are asserted by `verify.sh` down
to their opening-hours rows and the workshop-role flag; clicking around them as
the owner turns the gate red, and it is then impossible to tell your edits from a
regression. (This already happened once.) `qa` is currently unseeded — the
seeders only accept the slugs `bella`, `noga`, `te`; any other slug is a KeyError.

**`MODRYN_SMS_DISABLED=1` is not optional.** It stamps the tenant with the
`modryn.twilio.disabled` flag — the *only* thing that stops it from sending real
text messages. The SMS sender falls back to `TWILIO_*` environment variables, so
an unflagged tenant on a shell that exports those can text a real phone. Never
export `TWILIO_*`, and never source `.env` — it holds live, unrotated credentials.
All three tenants here carry the flag.

**Rebuilding from scratch** — README's "Setup from scratch" is the authoritative
list, run inside WSL:

```bash
./scripts/build_template.sh                   # golden database
MODRYN_SMS_DISABLED=1 ./scripts/new_boutique.sh bella "Bella Bridal"
MODRYN_SMS_DISABLED=1 ./scripts/new_boutique.sh noga  "Noga Couture"
MODRYN_SMS_DISABLED=1 ./scripts/new_boutique.sh qa    "QA — not a boutique"
# then per tenant, via odoo-bin shell:
#   seed_catalog.py, seed_staff.py, seed_demo_web.py, seed_atelier.py
```

`seed_demo_web.py` and `seed_atelier.py` are missing from the README but are
**required for a green `verify.sh`** — check 2172 fails with "seed_demo_web.py has
not run here" without the first.

No password lives in this repo — you pick your own. The local demo password is
kept **outside the repo** in the session scratchpad
(`modryn-local-secrets.sh`), and is both the back-office `admin` password and
every staff login. Seeded staff logins: **bella** = miri (owner), sara (manager),
rotem / noa / orly (staff) · **noga** = tamar (owner), yael (manager), dana (staff).

**Known-red baseline** (2026-08-26): `verify.sh` reports **359 passed, 6 failed,
7 skipped** on a clean install. Five failures are one root cause — English is
never installed (`scripts/build_template.sh:49` sets `language_ids` to `[he, ar]`;
the identical bug sits in the off-limits `deploy/scripts/build_template_prod.sh:134`).
The sixth is a self-declared dead check. Treat that as the floor: anything beyond
it is a regression you caused.

## The workflow — every task, no exceptions

Never code blind, never claim "done" without proof. Scale the paperwork to the
task, not the other way around:

| Task size | What to do |
|---|---|
| Tiny fix (one file, obvious) | Research the code first, state a 2–3 line plan in your reply, build, verify |
| A real feature (up to ~a day) | `/spartan:spec` → `/spartan:plan` → `/spartan:build`, then verify |
| Big work (several features) | `/spartan:epic` first, then spec → plan → build each piece |

"Research" means actually reading the code and README before proposing anything —
this codebase is full of traps that fail silently (see "Traps worth knowing" in
README.md).

## Localhost only — never the live demo

Every change gets built and proven on **this machine**. There is a live demo deployed at
`odoo-demo-production-1e73.up.railway.app`; it is not yours to touch.

- **After any change — however small — start the local server and open the page you
  changed**, on `http://bella.localtest.me:8069` or `http://noga.localtest.me:8069`.
  Look at it. Confirm the thing you changed actually does what you said it does.
- **Never open, `curl`, fetch, screenshot, or "just quickly check" the deployed URL** —
  not to compare, not to confirm, not because localhost is being slow. That host is the
  real deployment and it **sends real SMS that cost money**.
- If the local server will not start, **fix the local server**. Falling back to the
  deployed site is not a fallback; it is the exact thing this rule exists to prevent.
- "Done" means *you watched it work on localhost*. Nothing else counts. Never report a
  change as finished on the strength of the code alone.

## Verify after building — always run everything

No scaling to task size. A one-word text fix and a whole feature get the **same** full
gate, every time, before you say a word about being finished:

1. **The change itself, in a browser.** Server running, affected page loaded on
   `*.localtest.me:8069`, change visibly working.
2. **Full check suite:** `MODRYN_DEMO_PASSWORD=… ./scripts/verify.sh` — export the
   demo password first (check 10a fails red and tells you if you forgot). Run it
   only with its defaults: **never set `BASE_HOST` or `BASE_SCHEME`** — those
   point it at a live server.
3. **Browser QA** (catches what curl can't — see [qa/README.md](qa/README.md)):
   ```bash
   cd qa && BASE_URL=http://qa.localtest.me:8069 \
     MODRYN_DEMO_PASSWORD=… npx playwright test --project=dev
   ```
   **The throwaway `qa` tenant — not bella, not noga.** The guard refuses to run
   write-tests against any tenant that does not carry the `modryn.twilio.disabled`
   flag, and fails closed if it can't read the flag. It used to be safe to point this
   at `noga` because noga held no Twilio credentials — **that is no longer true.** The
   credentials moved into the Odoo process environment, so every database inherits them
   and holding none proves nothing. Safety is now opt-**out**: a tenant is safe because
   somebody *set* the flag. Write-tests also mutate state that `verify.sh` then checks,
   which is the second reason they belong on a disposable tenant. Provision it once:
   `MODRYN_SMS_DISABLED=1 ./scripts/new_boutique.sh qa "QA — not a boutique"`.
4. **If you changed what any route accepts or returns**, also grep
   `loadtest/k6/` and `qa/specs/` for that route and update them. The k6 checks
   assert only on status codes and coarse page markers, so a route that keeps its
   status but changes meaning stays green while testing nothing.

If any of these go red the task is **not** done — fix it, or say plainly that it is
broken and why. Never report green without having actually run them.

Dev-loop rules that save hours:

- Changed XML views/data → `./odoo/odoo-bin server -c odoo.conf -u <module> --stop-after-init`, then start the server again.
- Added a **new Python file or a new route** → full server restart. A module
  upgrade does not re-import Python.
- Changed static OWL/JS assets → restart the server.
- Only one server can hold port 8069 and the databases — never run two, and
  never restart it while something else is mid-check.
- OTP login flows can be driven headlessly: `./scripts/otp_code.sh <db> <phone-e164>`.
- Anything that would send an SMS: test on the **`qa`** tenant — it carries the
  `modryn.twilio.disabled` flag, so messages get logged instead of sent. Do **not**
  assume `noga` is safe any more; see "Verify after building" above for why.

## Git — the hard rules

- **Never commit to `main`.** Before every commit run `git branch --show-current`;
  if it says `main`, create a branch first: `feature/<short-name>` or
  `fix/<short-name>`.
- **Never push. Never merge. Never touch remotes.** All work stays on local
  branches. Mrwen pulls, reviews, and merges by hand. The `/spartan:build` and
  `/spartan:pr-ready` workflows end with a push-and-open-PR step — **stop before
  that step**; your work ends at the local commit. If the human asks you to push
  or merge, explain simply that only Mrwen does that, and stop.
- Commit messages: one short line saying what changed and why.

## Off-limits — do not run, do not edit, do not "fix"

These touch the **real, live** demo deployment, **real SMS that cost money**, or
**destroy databases**. Reading them for context is fine; running or editing them
is not. If a task seems to need one, stop and tell your human — in simple words —
to ask Mrwen:

- `deploy/` — the whole folder (provisioning, nginx, backups, restore, `deploy/scripts/*` including `new_boutique_prod.sh`)
- `railway.toml`, `.railwayignore`, anything mentioning Railway
- `scripts/configure_twilio.py` and `scripts/migrate_twilio_to_platform.sh` — real Twilio credentials
- `scripts/smoke_remote.sh` — pokes the live server
- `loadtest/seed/*.sh` — they `dropdb --force` and delete filestores; one once destroyed a live boutique with no undo
- **The live demo — `https://odoo-demo-production-1e73.up.railway.app`.** Do not open it, curl it, fetch it, screenshot it, or test against it. Ever. It is the real deployment and it sends real SMS that cost money. See "Localhost only" above.
- Any other command pointing at a URL that isn't `*.localtest.me` — treat every other host as production

Everything else under `addons/`, `scripts/` (the local ones), and `qa/` is yours
to work on — on a branch, with the workflow above.
