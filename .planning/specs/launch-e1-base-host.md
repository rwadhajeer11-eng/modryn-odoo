# E1 — `BASE_HOST`: point the regression suite at a real box

_Closes launch gate 1. Named in [`launch-readiness.md`](launch-readiness.md) §A7 as the last blocker._

## The problem

`scripts/verify.sh` hardcodes `bella.localtest.me` and `noga.localtest.me` at lines 11-12. Gate 1
says to run it "against the production box", and it cannot be. `deploy/README.md` §1e gives the
workaround — `/etc/hosts` entries pointing both names at `127.0.0.1` — and states the consequence
plainly: **the suite then exercises Odoo directly and bypasses nginx entirely.**

That is not a small caveat. Everything between the internet and Odoo — TLS, the catch-all, the
rate limits, `proxy_mode`, the `X-Forwarded-Host` that ProxyFix is gated on — is unverified by the
only suite anyone runs. `deploy/scripts/deploy.sh:104` calls `verify.sh` as its rollback gate, so
today a deploy is gated on 328 checks that never touch the thing serving the customers.

## What changes

`BASE_HOST`, `BASE_SCHEME`, `ODOO_CONF` — three environment variables, defaults preserving today's
behaviour exactly.

```bash
(dev)   ./scripts/verify.sh
(prod)  BASE_HOST=example.com BASE_SCHEME=https ODOO_CONF=/etc/odoo/odoo.conf \
        MODRYN_DEMO_PASSWORD=… /opt/modryn/scripts/verify.sh
```

## Blast radius, counted rather than estimated

All 68 uses are `$BELLA<suffix>` / `$NOGA<suffix>` — `$BELLA/shop`, `$BELLA$path`, `${BELLA}`.
**Not one of them constructs a hostname independently.** So the two host constants are a two-line
change and a `turl()` builder, and the rest of the file is untouched.

| Line | Change |
|---|---|
| 10 | `BASE_PORT=` → the scheme/host/port block and `turl()` |
| 11-12 | `BELLA=`/`NOGA=` → `$(turl bella)` / `$(turl noga)` |
| 74 | the "start it" hint gains the production invocation |
| 95, 957 | `odoo.conf` → `"$ODOO_CONF"` |
| 1611 | `"http://$db.localtest.me:$BASE_PORT/book"` → `"$(turl "$db")/book"` |
| after 8 | `cd` to the repository root |
| after 104 | the bella/noga membership gate |

Nine lines.

## Three constraints, each a way this could pass while lying

**No `-k`, anywhere.** Pointed at `https://` with `-k`, an expired certificate, a wrong-name
certificate and the self-signed placeholder `provision.sh` installs at bootstrap all look
identical to a healthy one. A certificate fault must fail loudly at §0 rather than produce 217
quiet passes. Certificate inspection is E2's job, deliberately.

**`ODOO_CONF` must be explicit on the box.** `/opt/modryn` is a checkout of this repository, so
`./odoo.conf` *exists there too* — and it is the developer's, listing `bella,noga,modryn_template`
with absolute paths to a laptop. Reading it on the box resolves a tenant list that has nothing to
do with what the server runs, and every per-tenant loop below then asserts against the wrong set.

**A `bella`/`noga` membership gate that exits, not skips.** The asymmetry between those two names
is load-bearing in **fourteen** `psql` call sites — ten `-d bella`, four `-d noga`. §5 reads
`modryn_alteration_task` on bella because noga holds zero *by design*; §16 reads
`modryn_shift_slot` on bella for the same reason. Point the suite at a box whose boutiques are
called something else and every one of those becomes `psql -d bella` against a database that does
not exist: `psql` writes to stderr, the `2>/dev/null || echo 0` idiom swallows it, and the
assertion reads a legitimate `0`. Green, on a check with no subject. Meanwhile `$NOGA/shop` hits
nginx's catch-all and 404s, and §0 would not catch it because §0 only probes `$BELLA`.

So both names must be **in** the resolved `$TENANTS` and the suite must refuse to continue
otherwise. Not `skip()` — a suite that cannot see its own subjects has verified nothing, and 217
green lines under those conditions is the single most dangerous output this file can produce.

**Derived by name, never positionally.** `BELLA=$(nth 1)` would silently swap the pair the day
someone reorders `db_name`, and the "noga legitimately holds zero" assertions would then run
against the tenant that has data — red, for a reason nobody could find. With a third boutique,
positional derivation quietly ignores it. The names are the contract.

## Then wire it into the deploy gate

`deploy/scripts/deploy.sh:104` invokes `verify.sh` with no `cd` and no environment. Today that
means relative `odoo.conf` and the ~30 `addons/` greps resolve against whatever directory root
was in when they typed `sudo`. It must pass `BASE_HOST`, `BASE_SCHEME`, `ODOO_CONF` and
`MODRYN_DEMO_PASSWORD` explicitly. That deletes the `/etc/hosts` workaround from `deploy/README.md`
§1e and the "this bypasses nginx" caveat at `deploy.sh:98-101`: §8's checks become an **addition**
rather than a **compensation**.

## The trap this creates, documented rather than fixed

With `BASE_SCHEME=https`, §10a's `POST /staff/login` goes through nginx, where it is rate-limited
(`modryn_otp`, `5r/m burst=3`) and watched by fail2ban (`maxretry=6`). The `geo` block exempts
`127.0.0.1/32` with the comment *"verify.sh drives /staff/login from the box itself"* — an
exemption that only applies when the suite runs **on the box**, because `$binary_remote_addr` is
nginx's peer. Run it from a laptop against production and §10a is limit-counted; run it three
times while debugging and you 429 yourself, and §10a then reports "sara could not sign in" with
entirely the wrong explanation.

`BASE_HOST` is therefore an **on-box contract**. Say so in the file, next to the variable.

## Acceptance

| # | Check | Required |
|---|---|---|
| 1 | `./scripts/verify.sh` unchanged on dev | 326 passed, 0 failed — count identical to baseline |
| 2 | `turl` builds both shapes | `https://bella.example.com` with **no** `:443`; `http://bella.localtest.me:8069` by default |
| 3 | `BASE_HOST=nonexistent.invalid ./scripts/verify.sh` | exit 1 at §0 |
| 4 | `ODOO_CONF=/dev/null ./scripts/verify.sh` | exit 1 at the tenant list |
| 5 | A tenant list without `noga` | exit 1 at the membership gate, naming the resolved list |
| 6 | `grep -c ' -k ' scripts/verify.sh` | 0 |
