# One Twilio sender for every tenant

**Spec**: none — this began as a direct request ("make all tenants use the same Twilio phone and
credentials") and the design decisions were taken inline. This file is the record.
**Epic**: none
**Created**: 2026-08-14
**Status**: built and verified

## The problem

Tenancy is one Postgres database per boutique. Twilio credentials were four
`ir.config_parameter` rows per database, read by one function — `ModrynSms._twilio_config()`
([addons/modryn_portal/models/sms.py](../../addons/modryn_portal/models/sms.py)). All four present
→ send; any missing → log the body and return `(True, 'logged')`.

`bella` held four; `noga` held none and silently texted nobody. Production provisioning already
piped **one platform-wide set** into each new tenant
([deploy/scripts/new_boutique_prod.sh](../../deploy/scripts/new_boutique_prod.sh)) — so the shared
sender was half-shipped, **by copy**. The copy was the problem: rotating meant rewriting N
databases, and a tenant provisioned before a rotation kept sending on the old key with no
configuration having changed anywhere a person would look.

Two facts shaped the design:

- **There is no inbound Twilio webhook anywhere in the repo.** Twilio is one-way outbound, a single
  `requests.post`. A shared `From` number therefore breaks no inbound routing — there is none.
- **Four harnesses asserted "zero `modryn.twilio.*` params ⇒ cannot send"**
  ([qa/lib/guard.js](../../qa/lib/guard.js), `loadtest/seed/gen_tenants.sh`,
  `reset_tenants.sh`, and `seed_tenant.py`'s phone-length defence). A process-wide fallback empties
  that property of meaning. Left alone, the Playwright suite would have texted real handsets.

## What was built

`_twilio_config()` resolves three levels, **each all-or-nothing**:

1. `modryn.twilio.disabled` set → `None` (log instead)
2. the tenant's own four params → use them (a boutique with its own sender identity)
3. the four `TWILIO_*` process environment variables → use them (the platform default)

All-or-nothing at each level is the load-bearing part: a half-filled tenant falls through *whole*
rather than mixing its `account_sid` with the platform's `from_number`, which would authenticate as
one boutique and arrive from another — and the recipient sees only the second.

`modryn.twilio.disabled` had to be a new key: Odoo's `get_param` returns the default for a stored
empty string, so no value writable into the existing four means "off" rather than "unconfigured".
Any non-empty value counts, **including the string `'0'`** — re-enable by clearing the parameter.

| File | Change |
|---|---|
| `addons/modryn_portal/models/sms.py` | `P_DISABLED`, `import os`, the three-level `_twilio_config()` |
| `deploy/systemd/odoo.service` | `EnvironmentFile=-/etc/modryn/deploy.env` — without it the fallback is dead in prod |
| `deploy/scripts/new_boutique_prod.sh` | stops writing per-tenant credentials; keeps the call for `modryn.cancellation_terms`; adds `MODRYN_SMS_DISABLED` |
| `scripts/new_boutique.sh` | `MODRYN_SMS_DISABLED=1` writes the flag at clone time |
| `scripts/migrate_twilio_to_platform.sh` | **new** — deletes per-tenant copies, never the flag; dry run by default, refuses `--apply` without the credentials present, prints the recovery path |
| `qa/lib/guard.js`, the two loadtest seeders | re-pointed from counting params to requiring the flag; still fail closed |
| `deploy/railway/bootstrap_restore.sh` | its `LIKE 'modryn.twilio.%'` DELETE now excludes the flag — that glob matches the off switch |
| `queue_entry.py`, `day_waitlist.py` | boutique name prefixed at the two chokepoints |
| `scripts/verify.sh` | §10k-quinquies — five states + a cross-tenant probe |

**Message bodies.** One shared number means a bride sees a number she does not know that may also
be texting her about a different store. `booking_comms.py` and `modryn_ops` already wove
`%(boutique)s` in; the queue's five bodies and the waitlist claim did not. Prefixed at
`queue_entry._send()` and `_make_offer` rather than editing six sentences — changing those msgids
would invalidate their he and ar translations, and `sync_translations.py` rewrites all eight addons
per run. A prefix touches no `.po`. OTP and staff texts deliberately untouched.

## Verification

`§10k-quinquies` executes the **real** `_twilio_config` and `_send_now` from source against a real
tenant database, planting parameters inside a transaction that is never committed — restoration is
the *absence of a commit*, so a crash mid-check still leaves the tenant untouched. It reads the
module's own constants, so renaming a key fails the check rather than being redefined as correct,
and it prints only where each credential came from, never a value.

| State | Required | Result |
|---|---|---|
| nothing configured anywhere | `None`, and `_send_now` → `(True,'logged')` | pass (the control) |
| no params, env set | the platform `from_number` | pass |
| flag set, env set | `None` — the flag beats a full environment | pass |
| tenant's four + env set | the **tenant's** `from_number` | pass |
| three params + env set | the env cfg **whole**, no mixing | pass |

Plus the cross-tenant probe: bella and noga, asked separately, resolve the *same* sender. That
claim is not one any single tenant can make about itself — the reasoning `.memory/odoo-traps.md`
§13 paid for.

Measured after the migration: `363 passed, 1 failed, 2 skipped`. The failure is four orphan
`res_partner` rows on noga from public-route QA runs on **2026-08-13**, a day before any file here
existed; §15's check is unmodified by this build. Both guard directions were exercised by hand —
`guard.js` refuses noga, and permits it with the flag set.

## What this does not do

- **It does not prove delivery.** `BACKLOG.md` #1 still stands: no message has ever reached a
  second handset. This build made an unproven channel available to *more* tenants.
- **It does not keep the guarantee's direction.** It inverted from opt-in-safe to opt-out-safe, and
  that is the real cost. See `BACKLOG.md` → "Left open by the shared-sender build".
- **It does not exercise the provisioning flag.** `MODRYN_SMS_DISABLED` is read at
  `new_boutique.sh:116`; no tenant has been created with it.
