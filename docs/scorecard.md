# Odoo evaluation scorecard

Built in one session against Odoo 19 Community. Every row below is backed by something
that ran, not by reading documentation. Screenshots in `docs/screenshots/`.

**Total custom code written: ~950 lines across 3 addons** (theme 271, booking 369,
queue 309), plus ~300 lines of provisioning/seed scripts.

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

---

## The eight questions

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | Can Odoo's storefront reach MODRYN's luxury bar? | 🟡 **Yellow** | Palette, fonts, radii and RTL all land through Odoo's *native* variable slots — no `!important`, no specificity war (`primary_variables.scss`, 40 lines). But `modryn.scss` needed 166 lines to undo eCommerce chrome, enforce the three-gold contrast rule Odoo's auto-contrast breaks, and fix heading weights. Header logo, footer boilerplate and Odoo branding remain unstyled. Reaching MODRYN's exact storefront would take days more, and the website builder fights bespoke editorial layout. |
| 2 | Is custom booking as good as MODRYN's? | 🟡 **Yellow** | Both PRD paths work end-to-end in **369 lines**: dress-bound (`/book/dress/<id>`, binds the *variant* so size travels) and standalone (`/book`). Terms enforced server-side (rejected submission preserved input, created 0 rows). Slot collision re-checked at submit. DST-correct: 10:00 Israel stored as 07:00Z. **But** this is a fixed Sun–Thu 10:00–18:00 grid — no availability engine, no capacity, no OTP, no deposit. Those are the expensive 80%. |
| 3 | Custom-addon velocity vs React/FastAPI | 🟢 **Green, with a tax** | Three working addons in one session. Model+view+controller inheritance is genuinely terse. The tax is *tacit knowledge*: three separate silent-failure traps (theme category killing assets, LibSass rejecting modern CSS, `mapped()` on empty recordsets) cost more time than the code did. A team fluent in Odoo would be fast; this team is not yet. |
| 4 | DB-per-tenant ops burden | 🔴 **Red at scale** | Provisioning works and takes ~20s: `createdb -T` + filestore copy + 4 fixups. Isolation is excellent (below). But every operation multiplies by N: module upgrades are an `-u` loop over N databases, backups are N dumps, crons run N times, and each tenant carries a **~74 MB** floor. At 2 boutiques this is elegant; at 50 it is a platform team's full-time job, and building the cross-tenant console back is Phase 6 (**L**). MODRYN's RLS gets this for free. |
| 5 | Which Enterprise modules are worth paying for? | 🟡 **Yellow** | `appointment` would replace maybe half of the Phase-2 booking engine (slotting/capacity) but not phone-OTP, terms versioning or deposits. `planning` covers roughly half the roster. `whatsapp` is a real saving. **But** Enterprise is per-user priced — awkward for many-tenants/few-users — and hosting it for customers requires an Odoo partner agreement. That is a commercial negotiation, not a purchase. |
| 6 | Hosting story | 🟡 **Yellow** | Local dev without Docker worked, at the cost of the five environment gotchas in the README. Production means: one Odoo serving N databases behind nginx with wildcard TLS, `list_db=False`, a gevent port for the websocket, and a worker for crons. Note **an unknown subdomain 303-redirects to Odoo's database manager**, not a clean 404 — `list_db=False` plus an nginx rule is mandatory, not optional. |
| 7 | bus.bus realtime vs MODRYN's 5s polling | 🟢 **Green** | The clearest win. **51 lines of OWL** gave a board that updates with no refresh: a walk-in created by a `curl` POST *outside the browser entirely* appeared on the open board over the websocket. No Pusher, no vendor, no polling budget. The websocket already runs for Discuss. This is materially better than the polling MODRYN settled for, and it is the one result that would change my mind about a floor-tools rebuild. |
| 8 | True cost of Arabic parity | 🟢 **Green** | Better than expected. `/ar/shop` serves `<html lang="ar-001" dir="rtl">` and **core UI is already Arabic** (بحث / ترتيب / المنتجات) at zero cost — that is the bulk of MODRYN's F45. Our own strings fall back to Hebrew and need one `.po` per addon; QWeb text nodes are extractable, so it is a translation job, not a refactor. Per-tenant *content* translation remains an operator cost either way. Caveat: Frank Ruhl Libre has no Arabic glyphs, so an Arabic display face is still needed. |

---

## Tenancy isolation — proven, not assumed

| Check | Result |
|---|---|
| Catalogs | Fully disjoint; bella's 3 dresses and noga's 2 never cross |
| Bella's product URL requested on noga | No leak |
| Authenticated session cookie replayed cross-tenant | `SessionExpiredException` — rejected |
| Bookings | Booking created on bella; noga's table stayed empty |
| Slot availability | 10:00 consumed on bella, still free on noga |
| Storage | Physically separate databases (`bella` 75 MB, `noga` 74 MB) |

**One caveat worth fixing before any pilot:** requesting bella's product URL on noga does
not 404 — Odoo's slug resolution falls back to the *same-numbered* local record, so the
visitor silently lands on a different boutique's dress. No data leaks, but shared links
and SEO both misbehave. MODRYN 404s correctly here.

---

## Phase map to full PRD parity

S ≤2d · M ≤1w · L 1–3w · XL >3w. "Enterprise buys it" never means all of it.

| Phase | Feature | Size | Community-custom vs Enterprise |
|---|---|---|---|
| 2 | Booking engine: hours→slots, capacity, types, versioned terms, cancel/reschedule links | **XL** | `appointment` buys ~half; the rest is ours regardless |
| 2 | Israeli PSP provider (Grow/Meshulam/Tranzila) + deposit | **M** each | Custom either way; per-DB tenancy makes per-tenant credentials free |
| 2 | Twilio SMS + phone OTP + 24h reminders (`ir.cron`) | **M** | Custom (native SMS is IAP-credits). Crons are per-database — N tenants self-schedule, a quiet win |
| 2 | Owner feature-toggle matrix | **S–M** | `res.config.settings` per tenant DB — trivial here |
| 3 | Client portal: OTP login, my bookings, `.ics` | **M** | `portal` buys the shell; phone-OTP is ours |
| 3 | Waitlist auto-reallocation | **M** | Custom |
| 4 | QR queue hardening · floor terminal · SOS | **M · M · S** | Custom; `frontdesk` is generic kiosk only. **The 51-line probe says these are cheap** |
| 5 | Weekly roster: availability, headcount targets, shortage alerts | **L** | `planning` buys ~half; submission workflow is ours (**M** residual) |
| 5 | Alterations kanban + seamstress capacity | **M** | `repair` buys states/kanban; capacity is ours |
| 6 | Platform provisioning console + wildcard TLS | **L** | Custom — this is the DB-per-tenant tax made visible |
| 6 | Arabic full parity | **M** | Core `ar` free; our strings + tenant content are the cost |

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
