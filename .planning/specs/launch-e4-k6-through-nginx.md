# E4 — a load harness that can measure through nginx

_Closes launch gate 9, and supplies the (a) half of the restated gate 10._

## The problem

`loadtest/` is a complete k6 harness — six ramp stages, five role scenarios, two focused tests,
thresholds derived from `load-test-spec.md`. It cannot be pointed at the box.

`guardTenants()` in `loadtest/k6/lib/session.js` fails closed unless **every** tenant matches the
shape `gen_tenants.sh` writes: slug `^[a-z]{1,8}[0-9]{2}$`, baseUrl
`^http://<slug>\.localtest\.me:[0-9]+$`, phonePrefix `+97252<NN>`, and a fleet-wide
`loadtestSecret`. That guard is correct and it exists for a good reason, spelled out at length in
its own comment: a run against a live boutique writes hundreds of invented customers onto a
working floor board mid-service, and staff cannot tell them from real arrivals.

But gates 9 and 10 need a **through-nginx** measurement. `deploy/README.md` §7 says to set
`LOADGEN_IP` for the rate-limit exemption, which only makes sense for traffic arriving through
nginx. So the load tenants must live on `$DOMAIN`, and the `localtest.me` anchor has to go.

## What that anchor was actually doing

Two jobs at once, and only one survives being pointed at production:

- **(a)** proving the manifest came out of `gen_tenants.sh` rather than being hand-edited;
- **(b)** proving the target cannot be a boutique — because `new_boutique_prod.sh` serves every
  boutique at `https://<slug>.$DOMAIN` and never on `localtest.me`.

Moving the fleet to `$DOMAIN` **deletes (b) entirely**, and no shape rule brings it back: `lt01` is
a perfectly legal boutique slug under `new_boutique_prod.sh`'s own grammar (lowercase,
letter-initial, 2-31 chars). Relaxing the regex and calling it done would leave a guard that reads
strict and defends nothing.

## The change, in two parts

**Part A — recompute, do not pattern-match.** `gen_tenants.sh` writes an `origin` field once for
the whole fleet. `loadTenantFault()` rebuilds `${scheme}://${slug}.${rest}` from the slug and that
origin, and compares for **equality**. A hand-edited `baseUrl` cannot pass without also being
exactly `<slug>.<origin>` for a slug that already satisfies the shape rule and a phonePrefix that
already satisfies `+97252<NN>`. `origin` comes from the **file**, never `__ENV` — the file is the
artifact this gate inspects, and an environment variable would let an operator retarget the
harness at runtime while the gate went on inspecting something else. This preserves (a) and
tightens it.

**Part B — a capability probe, because shape is no longer enough.** The property that actually
distinguishes a load tenant from a boutique is *"this database has the staging capture addon
installed and enabled"* — and a boutique **cannot** have it, because `loadtest/odoo_addons` is not
on production's `addons_path`, so the module is not even discoverable there. That is a capability,
not a name.

`/loadtest/otp` cannot serve as that probe: every refusal is deliberately the same 404, which is
exactly what makes it useless as evidence. Add `/loadtest/ping` — 200 only where the module is
installed AND the enabled flag is set AND the secret matches, the same three gates `read_code`
already uses, so it leaks nothing new. Call it for **every** tenant from `setup()` in
`loadtest/k6/main.js`: the init context cannot make requests, and `setup()` runs before any VU
writes a row, so the run dies before the first invented customer.

## On-box versus external, and why it is forced

`modryn-http.conf:61-73` exempts `127.0.0.1/32` from `limit_req` unconditionally. **k6 on the box
therefore makes gate 10 structurally unfailable** — the generator is exempt by construction, so its
429 count is zero on a box with no rate limiting at all, and the gate reports the same green either
way.

On-box also costs the measurement: 400 VUs of k6 plus TLS termination compete with 48 Odoo workers
and Postgres for the same 32 threads, so `rt` and `urt` in `modryn.log` describe a box running a
load generator, not a box serving customers.

**Run from an external generator.** The cost is honest and stateable — RTT, jitter and the
generator's own bandwidth ceiling are inside the numbers. Subtract them the way
`modryn-http.conf` already says: `rt - urt` is queueing plus network, `urt` alone is Odoo.

## Sequencing: the whole campaign runs before the first real boutique

This is the most important decision in the feature and it costs nothing but ordering. Once the
`localtest.me` anchor is gone and the fleet lives on the production domain, running load tenants
alongside live brides is the one arrangement in this epic where a single mistake writes invented
customers onto a working floor board. If no boutique exists yet, there is nothing for a mis-shaped
`tenants.json` to reach.

```
A  empty box: provision, template built, db_name empty, anything.$DOMAIN -> 404
B  load fleet: gen_tenants.sh 30 -> ramp from an external generator -> final
   validation ramp with LOADGEN_IP CLEARED
C  teardown, proven (see below)
D  real boutiques, with the rotated Twilio credentials
```

`gen_tenants.sh` is laptop-shaped in four places: `-c odoo.conf` (relative → the *dev* config),
`./scripts/new_boutique.sh` (dev clone, dev filestore), the hardcoded
`http://%s.localtest.me:%s` in the manifest writer, and `reset_tenants.sh`'s
`FILESTORE="$REPO/.odoo-data/filestore"`. Four environment variables — `ODOO_CONF`,
`NEW_BOUTIQUE`, `BASE_SCHEME`+`BASE_DOMAIN`, `FILESTORE` — about ten lines.

**Do not fork the script.** `reset_tenants.sh` and `make_gold.sh` both read `$ODOO_CONF`'s
`db_name` as their definition of "live tenant, refuse to drop". A fork is how that safety diverges.

## Teardown proof

Absence-of-evidence dominates, so each check needs a control. In particular a `/loadtest/ping` 404
proves nothing — that route 404s for a wrong secret and a disabled flag too, by design. The checks
with teeth are the `ir_module_module` count (**absent**, not "uninstalled") across every surviving
database *including the template every future boutique is cloned from*, and the running process's
own cmdline rather than the unit file.

And one thing captured **before** teardown, so the zeros have a non-zero to be measured against:
`curl "https://lt01.$DOMAIN/loadtest/ping?secret=…"` returning `{"loadtest":true}`. A column of
zeros with nothing that was ever non-zero is not evidence of removal.

## Acceptance

| # | Check | Required |
|---|---|---|
| 1 | `node loadtest/k6/lib/session.check.mjs` | rejects `https://bella.example.com` under origin `https://example.com` |
| 2 | same | rejects a slug-matching URL under a *different* origin |
| 3 | same | accepts `https://lt01.example.com` under origin `https://example.com` |
| 4 | same | rejects a manifest with no `origin` at all |
| 5 | legacy dev manifest (`http://lt01.localtest.me:8069`, origin `http://localtest.me:8069`) | still accepted |
| 6 | `/loadtest/ping` without the secret | 404, identical to a wrong secret |
| 7 | `main.js` `setup()` | probes **every** tenant, not a sample |
