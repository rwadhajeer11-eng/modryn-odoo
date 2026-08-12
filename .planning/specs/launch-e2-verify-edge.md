# E2 — `verify_edge.sh`: the layer `verify.sh` structurally cannot see

_Closes launch gates 2, 3, 3b, 3c, 4, and supplies the control half of gate 10._

## The problem

`verify.sh` speaks to one hostname that exists. The nginx layer is defined almost entirely by what
happens to hostnames and paths that **should not** work: the catch-all, `^~` versus regex location
precedence, `limit_req`, `internal`, the certificate. Nothing in `verify.sh`'s model can reach any
of it, and after E1 it still cannot — pointing it at `https://` proves TLS terminates, not that an
unknown subdomain dies before Python.

That material exists. It is `deploy/README.md` §8 and `launch-readiness.md` §C8/C9, and it is
**prose** — a copy-paste block with expected values in comments. It produces no exit code, so it
cannot gate a deploy, and nobody can tell afterwards whether it was run or skimmed.

## The check in it that cannot fail

§8 check 1 runs `journalctl -u odoo --since '1 min ago' | grep nope` and expects nothing.
`deploy/odoo.conf.prod:208` sets `log_level = warn`; the only handler override is
`odoo.addons.base.models.res_users:INFO`. **Production Odoo logs no request lines at all.** That
grep is empty whether or not the request reached Python — including in the precise failure it
exists to catch, where an unknown subdomain 303s to `/odoo` and then to
`/web/database/selector`, raising nothing.

The answer is in the nginx access log, not Odoo's. `modryn-http.conf:39` already logs
`ua_status=$upstream_status`, which is `-` when no upstream was contacted and a number when one
was. That is a **positive fact about the request**, not the absence of one — and it needs its own
control, because `ua_status=-` is also what a broken or renamed log field produces.

## Twelve checks

| | Check | Mode |
|---|---|---|
| E1 | unknown subdomain → 404, **zero redirect hops**, `ua_status=-` in `modryn.log`, plus a control proving a proxied request logs a *numeric* `ua_status` seconds later | remote + on-box |
| E2 | `/web/database/{manager,selector}` → 404 on the tenant host **and** an unknown host, bare and under `/en/` `/he/` `/ar/` | remote |
| E3 | positive control: prefixed routes answer **400** (registered, CSRF refused), not 404 | remote |
| E4 | 12 CSRF-less POSTs to `/waitlist/join` and `/en/waitlist/join` → tail is 429 | remote |
| E5 | port 8069 refused from outside | remote is the real test |
| E6 | zero connections to `modryn_template` | on-box |
| E7 | gzip on the HTML **and** on the CSS bundle | remote |
| E8 | `/web/filestore/…` → 404 (`internal`) | remote |
| E9 | certificate chain verifies against the system store **and** `-checkend 30d` | remote |
| E10 | HSTS `max-age=31536000; includeSubDomains`, `nosniff` | remote |
| E11 | `fail2ban-regex` reports `1 matched`, **and** the captured host is the real client | on-box |
| E12 | `:80` → 301 https; ACME path 404s from `/var/www/acme` rather than redirecting | remote |

`--remote-only` runs the eight pure-HTTP checks and `skip()`s the four on-box ones **with the
reason printed**, so a green laptop run can never be mistaken for a full pass.

## Design rules, each one a failure someone already had

**Derive the tenant. Never assume `bella`.** An unknown host 404s at the catch-all, so a hardcoded
slug on a box that does not have one makes E2, E4, E7 and E8 assert against the catch-all and pass
for entirely the wrong reason. Read the first entry of `db_name` on-box; require `TENANT=` in
remote mode rather than guessing. Abort immediately if `$T/shop` is not 200 — every check below
would otherwise be measuring the catch-all.

**`-k` on purpose here, and only here.** E9 inspects the certificate deliberately, so the other
eleven must keep working on a box whose certificate is bad. Otherwise one expired cert turns
twelve independent results into a single useless error and you learn nothing about the twelve.

**E3 runs before E4.** `modryn_post` is `10r/m` keyed on client IP. Once E4 has spent the bucket,
the control that proves the prefixed route exists would itself answer 429, and there is no way
left to establish it was ever there. A 429 on a path nginx would 404 anyway proves nothing.

**Language prefixes are not decoration.** `deploy/README.md` "Where the spec was wrong" #11 and #12
record both bypasses: every MODRYN route is `website=True`, so Odoo answers at `/path` *and*
`/<lang>/path` and renders the prefixed form into its own `<form action>`. `location = /book/submit`
matched the bare form only, and `/book/submit` and `/waitlist/join` each send an SMS — `limit_req`
was the entire defence for the Twilio bill. `/en/web/database/manager` was served byte-identically
to the bare form (verified with `curl` + `cmp`: two 46,329-byte responses).

## Safety, stated at the top of the file

Two checks are not read-only, and both are bounded:

- **E4** spends the operator's own `modryn_post` bucket for about a minute, shared across
  `/book/submit`, `/waitlist/join`, `/claim/*` and `/my/cancel/*`. **Zero rows written and zero
  SMS**: no `csrf_token` is sent, so Odoo rejects at 400 before any handler runs. The moment
  someone "improves" this by scraping a token, every accepted POST becomes a real message.
- **E11** may generate **one** failed login — 1 of `maxretry=6`, toward a one-hour ban of the
  operator's IP on ports 80/443 (SSH is unaffected; the jail's `port = http,https`). Never loop it.
  It is self-limiting: the line it produces makes the next run take the other branch.

Everything else is a GET or a socket probe.

## Gate 10, restated so it can fail

`deploy/README.md:507` runs `grep ' 429 '` against a log that writes `status=429`. That matches
nothing on any box. And with `LOADGEN_IP` exempted per §7, the generator is structurally incapable
of producing a 429, so zero is guaranteed on a box with no rate limiting at all.

What the gate is *trying* to assert is *the limits are not so tight that real traffic trips them*.
Two numbers, neither meaning anything alone:

- **(a)** `status=429` count from clients that are **not** the exempted generator, in the ramp
  window → required 0.
- **(b)** E4 passed in the same session → proof that 429 is reachable at all.

Note the log format carries no `$remote_addr`, so (a) needs either `client=$remote_addr` added to
`log_format modryn_load` — a versioned change, since the format is a declared contract with the
harness — or `/var/log/nginx/access.log` for that one number. Flagged, not silently chosen.

## Acceptance

| # | Check | Required |
|---|---|---|
| 1 | `bash -n deploy/scripts/verify_edge.sh` | clean |
| 2 | `--remote-only` against a host with no box | fails at the tenant probe, not with 12 confusing errors |
| 3 | `--remote-only` on a real box | 8 pass, 4 `skip()` **with reasons printed** |
| 4 | on-box, full run | 12 pass, exit 0 |
| 5 | E3 appears before E4 in execution order | verified by reading the output order |
| 6 | the safety header names E4 and E11 explicitly | present in the file |
