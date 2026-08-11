# Odoo evaluation scorecard

Built against Odoo 19 Community over several sessions. Every row below is backed by
something that ran, not by reading documentation. Screenshots in `docs/screenshots/`;
the guided tour is [`walkthrough.md`](walkthrough.md); `scripts/verify.sh` re-checks the
whole thing in one command (**85 checks, all green** at the time of writing).

**Total custom code: ~6,400 non-blank lines across 7 addons** — 3,473 Python, 1,898 XML,
1,040 JS/SCSS — plus provisioning and seed scripts. The addons are `modryn_theme`,
`modryn_booking`, `modryn_queue_poc`, `modryn_staff`, `modryn_portal`, `modryn_atelier`
and `modryn_roster`.

---

## Verdict

**Odoo Community is a credible foundation for the catalog + storefront + back-office
half of the PRD, and a poor bargain for the half that makes MODRYN distinctive.**

The parts that map onto commerce primitives — multi-tenant storefronts, dress catalog
with a size/quantity matrix, Hebrew/Arabic RTL, staff back office, a themable public
site — came up in hours and are genuinely good. Odoo gave away for free: Hebrew *and*
Arabic UI translations, RTL asset compilation, an Israeli week (Hebrew's `week_start`
is Sunday natively), ILS with agorot, product variants with per-variant stock, a
websocket bus, and QR generation.

The parts the PRD actually differentiates on — booking, queue, floor board, SOS, roster,
Israeli payments, WhatsApp — are custom development on Odoo exactly as they were custom
development on FastAPI. Odoo Community has no appointment module, no planning module and
no WhatsApp module; those are Enterprise, which additionally requires an Odoo partner
agreement to host for third parties.

**Given MODRYN has 45 features merged and roughly 11 days of queued work left, a rebuild
is not justified by this PoC.** The defensible use of Odoo is as a *back-office option
per boutique* (stock, purchasing, invoicing, HR) alongside MODRYN — not as a replacement
for the storefront and floor tools.

**The extended build did not change that verdict, and it sharpened it.** Everything the
PRD differentiates on was subsequently built — comms, the premium waitlist, the refill
loop, fitting rooms, SOS paging, the weekly roster — and all of it was ordinary custom
development that Odoo neither helped nor hindered much. Odoo contributed the ORM, the
cron scheduler, the websocket bus and the translation pipeline; it contributed nothing
domain-specific. The one place it was decisively better than MODRYN remains `bus.bus`:
every realtime surface here — the floor board, the SOS overlay — rides one channel and
about fifty lines, against MODRYN's five-second polling.

---

## The eight questions

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | Can Odoo's storefront reach MODRYN's luxury bar? | 🟡 **Yellow** | Palette, fonts, radii and RTL all land through Odoo's *native* variable slots — no `!important`, no specificity war (`primary_variables.scss`, 97 lines — 72 of theming, plus the deduplicated `$modryn-*` palette that used to be redeclared in two staff stylesheets). But `modryn.scss` needed a further 112 to undo eCommerce chrome, enforce the three-gold contrast rule Odoo's auto-contrast breaks, and fix heading weights. Header logo, footer boilerplate and Odoo branding remain unstyled. Reaching MODRYN's exact storefront would take days more, and the website builder fights bespoke editorial layout. |
| 2 | Is custom booking as good as MODRYN's? | 🟡 **Yellow** | Both PRD paths worked end-to-end in **369 lines** as first built (the addon is ~460 now, after cancellation-aware slots and the waitlist form): dress-bound (`/book/dress/<id>`, binds the *variant* so size travels) and standalone (`/book`). Terms enforced server-side (rejected submission preserved input, created 0 rows). Slot collision re-checked at submit. DST-correct: 10:00 Israel stored as 07:00Z. **But** this is a fixed Sun–Thu 10:00–18:00 grid — no availability engine, no capacity, no OTP, no deposit. Those are the expensive 80%. |
| 3 | Custom-addon velocity vs React/FastAPI | 🟢 **Green, with a tax** | Three working addons in one session. Model+view+controller inheritance is genuinely terse. The tax is *tacit knowledge*: three separate silent-failure traps (theme category killing assets, LibSass rejecting modern CSS, `mapped()` on empty recordsets) cost more time than the code did. A team fluent in Odoo would be fast; this team is not yet. |
| 4 | DB-per-tenant ops burden | 🔴 **Red at scale** | Provisioning works and takes ~20s: `createdb -T` + filestore copy + 4 fixups. Isolation is excellent (below). But every operation multiplies by N: module upgrades are an `-u` loop over N databases, backups are N dumps, crons run N times, and each tenant carries a **~74 MB** floor. At 2 boutiques this is elegant; at 50 it is a platform team's full-time job, and building the cross-tenant console back is Phase 6 (**L**). MODRYN's RLS gets this for free. |
| 5 | Which Enterprise modules are worth paying for? | 🟡 **Yellow** | `appointment` would replace maybe half of the Phase-2 booking engine (slotting/capacity) but not phone-OTP, terms versioning or deposits. `planning` covers roughly half the roster. `whatsapp` is a real saving. **But** Enterprise is per-user priced — awkward for many-tenants/few-users — and hosting it for customers requires an Odoo partner agreement. That is a commercial negotiation, not a purchase. |
| 6 | Hosting story | 🟡 **Yellow** | Local dev without Docker worked, at the cost of the five environment gotchas in the README. Production means: one Odoo serving N databases behind nginx with wildcard TLS, `list_db=False`, a gevent port for the websocket, and a worker for crons. Note **an unknown subdomain 303-redirects to Odoo's database manager**, not a clean 404 — `list_db=False` plus an nginx rule is mandatory, not optional. |
| 7 | bus.bus realtime vs MODRYN's 5s polling | 🟢 **Green** | The clearest win. **51 lines of OWL** gave a board that updates with no refresh (that was the original probe; the same component is ~305 lines now that it carries drag-and-drop, rooms and paging): a walk-in created by a `curl` POST *outside the browser entirely* appeared on the open board over the websocket. No Pusher, no vendor, no polling budget. The websocket already runs for Discuss. This is materially better than the polling MODRYN settled for, and it is the one result that would change my mind about a floor-tools rebuild. |
| 8 | True cost of Arabic parity | 🟢 **Green** | Better than expected. `/ar/shop` serves `<html lang="ar-001" dir="rtl">` and **core UI is already Arabic** (بحث / ترتيب / المنتجات) at zero cost — that is the bulk of MODRYN's F45. Our own strings fall back to Hebrew and need one `.po` per addon; QWeb text nodes are extractable, so it is a translation job, not a refactor. Per-tenant *content* translation remains an operator cost either way. Caveat: Frank Ruhl Libre has no Arabic glyphs, so an Arabic display face is still needed. |

---

## Tenancy isolation — proven, not assumed

| Check | Result |
|---|---|
| Catalogs | Fully disjoint; bella's 3 dresses and noga's 2 never cross |
| Bella's product URL requested on noga | 404s (fixed — it used to 301 onto noga's same-numbered dress) |
| Authenticated session cookie replayed cross-tenant | `SessionExpiredException` — rejected |
| Bookings | Booking created on bella; noga's table stayed empty |
| Slot availability | 10:00 consumed on bella, still free on noga |
| Storage | Physically separate databases (`bella` 75 MB, `noga` 74 MB) |

**The caveat this table used to carry is now fixed.** Requesting bella's product URL on
noga did not 404: a `<model(...)>` route matches on the id and discards the slug's
name-half, so `http_routing` found the URL non-canonical and 301'd onto noga's
*same-numbered* dress. Nothing leaked — the record was noga's own — but a shared link
landed the visitor in the wrong boutique. `modryn_theme`'s `ir.http._pre_dispatch` now
compares the requested slug against the record's canonical one and 404s on a mismatch;
`verify.sh` §1 probes it in both directions with a positive control. Odoo's bare-id
`/shop/<id>` → canonical 301 is deliberately kept, because it is load-bearing for SEO.

The comparison runs against **every language the site publishes**, request-language first.
An earlier cut compared only the request's language, which looked safe because `curl` always
saw 200 — curl's user-agent trips `is_a_bot()` in `http_routing`, which pins `request.lang`
to the default. A real browser sending `Accept-Language: en-US` is 303'd to the `/en/` form
*before* the guard runs, so the boutique's own canonical Hebrew link would have arrived to be
compared in English. Latent only because `name->>'he_IL'` and `name->>'en_US'` hold the same
string today; the day the owner translated one dress name, the canonical link would have
404'd for every English-defaulting first-time visitor. `verify.sh` §1 now probes the `/en`
and `/ar` forms too.

**One ceiling that remains, stated rather than hidden:** a *stale* slug inside one tenant
404s where it used to 301 onto the canonical URL. Cross-tenant and same-tenant-stale are
textually identical — a name that is not this record's name — and there is no rename history
to separate them (`website_rewrite` is empty in both databases), so the choices were "404
every mismatch" or "leave the tenancy hole open". Renaming a published dress therefore
breaks its indexed URLs. Cheap to revisit: one `website.rewrite` row per rename restores the
301, and Odoo already honours those.

---

## Phase map to full PRD parity

S ≤2d · M ≤1w · L 1–3w · XL >3w. "Enterprise buys it" never means all of it.

| Phase | Feature | Size | Community-custom vs Enterprise |
|---|---|---|---|
| 2 | Booking engine: hours→slots, capacity, types, versioned terms, cancel/reschedule links | **XL** | `appointment` buys ~half; the rest is ours regardless |
| 2 | Israeli PSP provider (Grow/Meshulam/Tranzila) + deposit | **M** each | Custom either way; per-DB tenancy makes per-tenant credentials free |
| 2 | Twilio SMS + phone OTP + 24h reminders (`ir.cron`) | ~~M~~ **built** | Custom, as predicted (native SMS is IAP-credits). ~250 lines across `sms.py`, `booking_comms.py` and the tokenised link controller: a thin Twilio client, an `ir.cron`, HMAC-signed confirm/cancel links. Crons being per-database is a real win — each tenant self-schedules. Twilio credentials are per-tenant config, free under DB-per-tenant. **Delivery proven only to Twilio's API**, not to a second handset |
| 2 | Owner feature-toggle matrix | **S–M** | `res.config.settings` per tenant DB — trivial here |
| 3 | Client portal: OTP login, my bookings, `.ics` | ~~M~~ **mostly built** | `portal` bought the shell; phone-OTP and "my bookings" are ours and done (codes stored hashed). `.ics` export is still outstanding |
| 3 | Waitlist auto-reallocation | ~~M~~ **built** | Custom, ~230 lines across `day_waitlist.py` and its controller. Day-level (not slot-level) waitlist, 2-hour exclusive claim window, one live offer per day, expiry cron passing it down the line |
| 4 | QR queue hardening · floor terminal · SOS | ~~M · M · S~~ **built** | Custom, and the 51-line probe was right — these were cheap. The queue became a Waitwhile-style intake with an invisible staff gate and three warm states; the floor board grew pointer-based drag-and-drop, fitting rooms and paging. SOS rides the same `bus.bus` channel as everything else |
| 5 | Weekly roster: availability, headcount targets, shortage alerts | ~~L~~ **built (M)** | Smaller than estimated — ~450 lines of Python (~780 including views and SCSS). Owner-defined shift templates, staff availability, per-role targets, shortage badges, publish-freezes-the-week. `planning` would have bought the grid, not the submission workflow. Does **not** yet feed the booking engine or restrict floor assignment |
| 5 | Alterations kanban + seamstress capacity | **M** | `repair` buys states/kanban; capacity is ours |
| 6 | Platform provisioning console + wildcard TLS | **L** | Custom — this is the DB-per-tenant tax made visible |
| 6 | Arabic full parity | **M** | Core `ar` free; our strings + tenant content are the cost |

---

## What the extended build taught that the first pass did not

1. **Odoo 19 removed things quietly.** `_sql_constraints` is gone — declaring one produces
   no index and no error, and a duplicate row sails through. `res.groups.category_id`
   became `privilege_id`. Neither failure announces itself.
2. **Translatable fields are jsonb, and that is contagious.** A `translate=True` Char
   cannot carry a uniqueness constraint (whole JSON objects get compared), switching it
   back does not migrate the column, and every `WHERE name = 'x'` against it fails.
3. **QWeb translation units include inline markup.** A hand-written msgid never matches;
   the only reliable source is Odoo's own POT export, which is why
   `scripts/sync_translations.py` re-keys translations onto exported msgids.
4. **Catching a ValidationError does not undo the write.** It stops Odoo's handler from
   rolling the request back, so a rejected value gets committed anyway. Constraint
   violations you intend to *report* rather than raise need an explicit savepoint.
5. **Odoo's own drag hook suppresses pointer-events page-wide.** `elementFromPoint`
   returns `<html>` mid-drag, so hit-testing has to be geometric.
6. **The test harness lies more often than the code.** A verify check comparing a naive
   UTC column against psql's local `now()` reported every cron three hours overdue;
   `create_date` is readonly so an ORM backdate is silently ignored; and anonymous 303s
   prove the gate, never the page — a 500 for signed-in staff passed every check until
   the suite learned to sign in.

---

## Open questions for the business

1. **Do boutique owners need to edit their own pages?** If no, the theme can be
   hard-coded QWeb and the ceiling in row 1 rises a lot. If yes, we inherit the website
   builder's constraints permanently.
2. **Is an Odoo partner agreement acceptable?** Every Enterprise-dependent row above is
   blocked on this commercial question, not a technical one.
3. **What is the realistic tenant count in 24 months?** Row 4 flips from elegant to
   expensive somewhere around 20–50 boutiques.
4. **Would boutiques pay for a real ERP back office** (stock valuation, purchasing,
   invoicing, payroll)? That — not the storefront — is where Odoo is worth more than what
   it costs to bend.
