# qa/

Browser QA. **The assertions curl cannot make.**

```bash
cd qa && npm install && npx playwright install chromium

# dev — MUST be a tenant with no Twilio credentials (see below)
MODRYN_DEMO_PASSWORD='…' BASE_URL=http://noga.localtest.me:8069 npm test

# production, read-only
BASE_URL=https://qa.$DOMAIN QA_TENANTS=qa QA_SSH=root@box npm run test:prod

# production, including the three acts that write
QA_ALLOW_WRITES=1 BASE_URL=https://qa.$DOMAIN QA_TENANTS=qa QA_SSH=root@box npm run test:prod
```

## Why this exists

`scripts/verify.sh` speaks HTTP and reads Postgres. It proves `/floor` returns
200. It cannot prove the board **paints** — a 200 carrying a JS exception that
leaves an empty div is exactly the shape of the bug §10a was written after, and
§10a still cannot see it.

Worse: **LibSass dies silently** on one Color-Level-4 `rgb(a b c / d)` and takes
the *entire* frontend bundle rather than the offending rule. `curl` sees a 200
with correct HTML and zero styling. Only a browser reports a computed value.

## The eighteen checks, and what each is for

| Spec | Catches |
|---|---|
| `storefront` acts 1, 1b, 1c, 2, 2b | the silent bundle collapse; RTL overflow at 375px; that `/he/`, `/en/` and `/ar/` compile to **different bundles**, so a theme change can break one and not the others |
| `booking` acts 3, 3b, 3c | that the grid the *browser was shown* is the grid the server accepts — the whole "slot offered, then refused" class; and that a refused submit wrote no row |
| `portal` act 4 | the OTP, the only auth path fail2ban cannot see |
| `staff` acts 5, 5b, 6 | that `/floor` paints; that staff-level access stops; and the **websocket** — `modryn-site.conf`'s `location ^~ /websocket` repeats all six `proxy_set_header` lines because nginx drops the inherited set, and losing one breaks *only* the floor board, *only* in production, with every HTTP check still green |
| `fonts` acts 7, 7b, 7c | launch gate 6, automated — with a control, because a page that requested no fonts at all also passes the first assertion |

## Three rules that do not bend

**1. Never run `@writes` against a tenant holding `modryn.twilio.*`.**
`lib/guard.js` enforces this in **every** environment and refuses the run. This
is not theoretical: `bella` carries four live parameters, and the first run of
this suite made real Twilio API calls from a laptop through act 3's booking
confirmation and act 4's OTP. `noga` carries zero and is the log-only tenant —
with no config, `modryn.sms._send_now` logs the body and returns `('logged')`.

**2. Never hardcode a phone number.** `lib/otp.js::qaPhone()` derives one from
the millisecond clock. A constant makes every writing spec run exactly once: the
walk-in it created is still *pending* on the board next time, the second
check-in is refused as a duplicate, the board does not change, and act 6 reports
the websocket broken when nothing was ever handed to it.

**3. Never consume the last seat of a day.** Act 3c picks a slot from a day that
has another one after it, and `test.skip`s when no such day exists. It used to
take the last slot on offer — the furthest day is also the emptiest, so it sold
that day out, a sold-out day correctly vanishes from `/book`, and `verify.sh`
§24 then reported *"open days missing from the page: 26.08.2026"*. The product
was right; §24 derives open days from the rota rather than from remaining
capacity. **A write-test that turns a legitimate state into a red line in the
suite that gates deploys is worse than no test.** Verified over three
consecutive qa→verify cycles: 328/0 each time, with act 3c skipping rather than
closing a day.

**4. Scope every submit to its own form.** `lib/form.js::submitFormWith()`.
`website.layout` renders a site-search form into the header of every frontend
page — twice — and each carries a hidden `<button type="submit">` that appears
*before* the real form. `form button[type=submit]` resolves to one of those on
every MODRYN page, and Playwright waits out its full timeout on an element that
will never be visible.

## Gotchas found while writing this

- **`expires_at > now()` is wrong.** Odoo stores Datetime as naive UTC in
  `timestamp without time zone`; Postgres `now()` casts to the server's local
  time. Measured here: a code issued at `19:53` UTC compared against a local
  `22:48` and read as expired. Use `now() at time zone 'UTC'`.
- **Logins are per tenant.** `res.users.login` is unique per *database*, and one
  database per boutique is the tenancy model. `bella` was seeded miri/sara/rotem
  and `noga` tamar/yael/dana, so a spec hardcoding `sara` only ran on bella —
  which is the tenant `@writes` must never use. See `lib/people.js`.
- **`workers: 1` is not a performance choice.** The booking spec takes a real
  slot from a shared grid and the queue spec asserts on a board every other
  test's writes land on.

## Production

Acts 3, 4 and 6 write. Excluding them from production would delete most of the
value, because they are precisely the ones curl cannot do — so run them against
a **dedicated throwaway tenant** instead:

```bash
sudo TWILIO_ACCOUNT_SID= TWILIO_API_KEY_SID= TWILIO_API_KEY_SECRET= TWILIO_FROM_NUMBER= \
  /opt/modryn/deploy/scripts/new_boutique_prod.sh qa "QA — not a boutique"
```

No per-test teardown, deliberately: the tenant is dropped after launch, and
`deploy/scripts/restore.sh` resets it from the previous night's dump in ~30s.
Teardown code that deletes bookings would have to be as correct as the product,
to undo rows nobody will ever look at.
