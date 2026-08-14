# Web technology + Odoo for customers, staff and advertising

Research report · 2026-08-14 · grounded in this repo (MODRYN, Odoo 19 Community, DB-per-tenant,
Hebrew-first Israeli bridal boutiques). Question as sharpened: *per area — customers/CRM, staff,
advertising/marketing, web presence — what does the current build already cover, what can Odoo add,
what needs outside web technology, and what is the recommended path for the unbuilt
advertising/marketing layer?* Researched via 8 parallel source sweeps (70 sources collected,
~30 cited below) plus a read of `scorecard.md`, `STATE.md`, `BACKLOG.md`, `bridal-ops.md` and
`.memory/decisions.md`. Settled repo decisions (Community-only, core never edited, no rebuild,
no leaderboards) are treated as fixed, not relitigated.

---

## TL;DR

**The licensing wall this repo assumed around marketing mostly is not there.** Email Marketing
(`mass_mailing` — with contact lists, templates, real A/B testing and opt-out management), SMS
Marketing (`mass_mailing_sms`), UTM/link tracking (`link_tracker`, `utm`, `website_links`), even
CRM's predictive lead scoring are all **free in Odoo 19 Community** — verified against the public
`odoo/odoo` 19.0 source, not vendor blogs (several of which claim the opposite and are wrong).
Enterprise only gates `marketing_automation` (drip workflows) and `social_marketing`, both
replaceable by thin custom addons in this repo's existing pattern.

The binding constraints are legal and operational, not licensing:

1. **Israeli spam law is an architecture requirement.** Up to ₪1,000 statutory damages *per
   message* without proof of harm, active class actions (one certified Dec 2025). The consent
   check belongs inside the send function, not in a marketing playbook.
2. **Never send email from the Odoo host.** Odoo's own docs say to relay through an ESP;
   Gmail/Yahoo have required SPF+DKIM from *all* senders since Feb 2024, and datacenter IPs start
   blacklisted. Per-tenant ESP relay is a launch requirement, not an optimization.
3. **WhatsApp's cheapest credible route is the Twilio channel already integrated for SMS** —
   not a BSP, not Enterprise. Utility messages cost ~$0.005 in Israel; the unverified-business
   cap (250 conversations/day) already covers a boutique, so Meta verification is a background
   task, not a launch blocker.
4. **Competitive parity is closer than expected.** BridalLive — the category leader at
   $99–249/mo — runs the *same* architecture MODRYN already has (trigger flows + BYO Twilio +
   external email). `modryn_ops` already sends thank-you / feedback / rebook flows. The visible
   gap is a **review-request flow** (BridalLive charges $119/mo for reputation management) and an
   email channel.
5. **For staff, buy nothing.** Planning/Appointments are Enterprise and the custom roster/booking
   already replace them. No Israeli payroll localization exists in *any* Odoo edition — payroll
   stays with the accountant (Michpal/Shiklulit); the one useful add is Community's
   `hr_attendance` kiosk for worked-hours export.

---

## Per-area map

| Area | Built today (this repo) | Odoo Community can add | Web technology adds | Recommendation |
|---|---|---|---|---|
| **Customers / CRM** | Booking (2 paths), portal + SMS OTP, walk-in queue, day waitlist, bride fields on `res.partner` (wedding date, gated budget, measurements), outcomes + follow-up SMS, conversion/ATV reports | `crm` (free, predictive scoring included) as a *pre-booking* capture bucket; website form → lead | Meta lead-ads webhook; Google Business Profile booking link | Stay calendar-first. `res.partner` remains the single source of bride truth; `crm.lead` only for not-yet-booked inquiries, converted by *linking* to the partner. Skip `crm` entirely if inquiry volume is negligible |
| **Advertising / marketing** | Transactional SMS only (Twilio; delivery to a handset still unproven — backlog #1) | `mass_mailing`, `mass_mailing_sms`, `link_tracker`/`utm`, `marketing_card` — all Community | Per-tenant ESP relay (Brevo/SES); Twilio WhatsApp channel; Meta lead ads + Click-to-WhatsApp; GBP + reviews | Consent gate first; then 2–3 pre-wired Hebrew flows (review request, dormant nudge, blast), **not** an exposed campaign builder |
| **Staff** | Roles + owner-run page-grant matrix, weekly roster feeding the booking grid, floor board + SOS over `bus.bus`, atelier auto-assignment, checklists with escalation | `hr_attendance` (kiosk check-in is in Community for 19) | None needed. Payroll stays external — accountant on Michpal/Shiklulit is the Israeli SMB norm | Add the attendance kiosk for hours-for-the-accountant. Do not license Enterprise: `planning` buys the grid the roster already has |
| **Web presence** | Luxury storefront, tri-language he/ar/en with RTL, public booking pages, tokenized self-service links | `website_links` (short links), `marketing_card` (shareable social cards) | Google Business Profile with booking link + systematic review collection; Instagram content | GBP link + review flow is the highest-ROI zero-code move — one tracked experiment put the booking link at ~20% of listing clicks |

---

## Key findings

**1. The Community/Enterprise line for marketing, verified at the source.** Presence in the public
LGPL-3 `odoo/odoo` 19.0 repo is the authoritative test. Present (Community): `mass_mailing`,
`mass_mailing_sms`, `link_tracker`, `utm`, `website_links`, `marketing_card`, `crm` (including
`crm_lead_scoring_frequency.py` — predictive lead scoring), `hr`, `hr_attendance` (with kiosk
templates), `hr_holidays`, `l10n_il`. Absent (Enterprise-only): `marketing_automation`,
`social_marketing`, `planning`, `appointment`, `whatsapp`, `hr_payroll`. Community `mass_mailing`
includes contact lists, drag-drop templates, A/B testing (`ab_testing_*` fields with a CHECK
constraint), opt-out reasons, blacklist and bounce tracking — all read directly in the 19.0
models. Several vendor blogs claim predictive lead scoring and parts of email marketing are
Enterprise-only; the official docs and source contradict them. *(Sources 1–4)*

**2. Enterprise would be a bad buy even where it applies.** Unlocking `marketing_automation` on a
self-hosted setup requires the **Custom** plan (~$20.40–25.50/user/month) — priced per user,
across every tenant, for drip workflows a ~100-line `ir.cron` addon replicates on top of
Community's own `mailing.trace`. This confirms and extends scorecard Q5. *(Sources 3, 1)*

**3. Israeli spam law (s.30A, "חוק הספאם") dictates the send-path design.** Marketing by SMS,
email — and per case law, WhatsApp — requires explicit prior opt-in consent. The existing-customer
exception needs three cumulative conditions (details given during purchase + advertising notice at
capture + similar-product scope + unexercised refusal) and is fragile for one-time bridal
purchases, so opt-in at booking/sale is the only robust path. Every marketing message must carry
"פרסומת", the boutique's identity, and a free opt-out that takes effect immediately. Exposure: up
to **₪1,000 per message** in statutory damages without proof of harm, plus class actions (one
certified 2025-12-14). Transactional messages (confirmations, reminders) are exempt under the
courts' "main objective" test **only while promotional copy stays out of them** — the current
`modryn_ops` templates comply; keep it that way. *(Sources 5–9)*

**4. Email needs an ESP relay per tenant — Odoo says so itself.** Official Odoo docs recommend
separate transactional and mass-mail servers (Postmark/Brevo; SES/Mailgun) rather than sending
from the host; DKIM is "required" for custom domains. Since Feb 2024 Gmail and Yahoo require
SPF+DKIM from all senders, and one-click unsubscribe + <0.3% complaint rate for bulk senders.
A boutique sends hundreds of emails a month — free ESP tiers cover it — but the all-sender auth
rules apply at any volume, and VPS IP ranges are frequently pre-blacklisted. *(Sources 10–12)*

**5. WhatsApp: Twilio channel first, direct Meta Cloud API later if ever.** Odoo's `whatsapp`
module is confirmed Enterprise-only ("does not work in Odoo Community edition"). Meta switched to
per-delivered-template pricing on 2025-07-01: utility templates (confirmations, reminders) cost
~$0.005 in Israel and are **free inside an open 24-hour service window**; marketing templates
~$0.035 (Israel figures from third-party rate summaries — verify against Meta's rate card before
pricing promises). Unverified businesses are capped at 250 business-initiated conversations per
24h — ample for a boutique — so tenants can start before Meta business verification completes
(expect days-to-weeks; legal-name mismatch is the #1 rejection cause). Twilio's WhatsApp channel
adds $0.005/message with no monthly fee and **reuses the account, credentials and error handling
already built in `sms.py`** — the smallest engineering lift by far. 360dialog's €49/month flat fee
only wins above ~10k messages/month, far beyond any boutique. The free WhatsApp Business App is
ruled out as a product feature (256-contact broadcast cap, no API). *(Sources 13–18)*

**6. Lead generation: two channels matter, and ingestion is a small custom addon.** In-vertical
Meta Ads benchmark (Lisbon bridal salon): 2,111 leads at ~$9.79/lead average, best sustained
campaign €5.75/lead, most leads arriving via WhatsApp — supporting Click-to-WhatsApp ads (which
also open a 72-hour free messaging window). Organic: 74% of couples follow a vendor on social
(The Knot 2024); reviews are the #1 vendor-selection factor, making Google Business Profile — with
its free booking link pointing at the tenant's own `/book` page — the best zero-cost move. Odoo
Community has **no native Meta connector** (that's `social_marketing`, Enterprise). The right
ingestion path is a small `http.Controller` addon: answer Meta's GET `hub.challenge`, validate
`X-Hub-Signature-256`, resolve `page_id` → tenant, fetch the lead via Graph API. Constraints to
design around from day one: Meta keeps lead data only 90 days, allows **one leadgen webhook
subscription per app** (the multi-tenant fan-out lives in that one endpoint), and every path that
touches lead data — including Zapier/n8n — needs Meta App Review for `leads_retrieval`, so start
the review early. Zapier's Lead Ads trigger is premium-priced per tenant and carries a documented
silent-failure profile; self-hosted n8n is a legitimate stopgap while App Review is pending.
*(Sources 19–24, 29)*

**7. The competitive bar is lower than it looks — and MODRYN is most of the way there.**
BridalLive ($99–249/mo, the category leader) ships marketing automation in all tiers as "Smart
Flows" — booked→confirm+remind, purchased→category+thank-you-task — on **BYO Twilio for SMS and
Mailchimp for email**: architecturally the same shape as `modryn_ops` + the SMS outbox. CloudBridal
($80–180/mo) gates SMS automation behind its top tier. The horizontal salon platforms monetize
harder: Boulevard bills $0.01/email and ~$2 per appointment booked from an automated campaign;
Fresha charges $0.05–0.10 per SMS recipient. Two conclusions: SMS-cost pass-through via the
tenant's Twilio is the accepted vertical norm (no bundled volumes needed), and the proven upsells
are **reputation/review management ($119/mo at BridalLive)** and a client portal ($110/mo) — one of
which MODRYN already has, and the other is a small flow away. *(Sources 25–28)*

**8. Staff: the research confirms "buy nothing," and adds one cheap piece.** `hr_attendance` in 19
Community includes the kiosk (PIN/badge tablet check-in) and overtime rules. No Israeli payroll
localization exists in any Odoo edition (the official localization list has ~30 countries; Israel
is absent), and the OCA community payroll port has no Israeli rules either — **nobody runs
compliant Israeli payroll natively in Odoo**. The Israeli SMB norm is accountant/bureau-run payroll
on Michpal or Shiklulit (Hilan upmarket); Israeli Odoo partners have privately built one-way
payroll-journal *import* modules, proving the integration direction. The boutique-sized touchpoint:
export worked hours from the kiosk for the accountant. *(Sources 30–33)*

**9. The most fatal risk isn't technical — it's abandonment.** The evidence base is soft on exact
percentages (flagged), but every source agrees SMB owners drop tools that require configuration.
BridalLive's differentiator is *defaults*, not capability. The mitigation is product design: ship
2–3 opinionated, pre-wired Hebrew-first flows and a one-screen blast tool; do not expose Odoo's
campaign builder to boutique owners. This matches the repo's own settled pattern (hardcoded flow
constants in `modryn_ops` instead of a configurable Smart-Flow engine). *(Sources 34, 12)*

---

## Recommended path

Ordered. Sizes use the repo convention (S ≤ 2d · M ≤ 1w · L 1–3w). Items 1–2 are prerequisites for
everything below them.

| # | What | Size | Why now |
|---|---|---|---|
| 1 | **Prove SMS delivery to a real handset + rotate the Twilio credentials** | S · blocked on you | Already backlog #1–2. Every flow below rides this channel; delivery is still unproven past Twilio's API |
| 2 | **Consent architecture** — opt-in fields on `res.partner` (flag, timestamp, source, revocation), enforced *inside* the shared send path; "פרסומת" prefix + free opt-out on anything classified marketing; transactional templates stay promo-free | S | s.30A exposure is ₪1,000/message; one bad blast to 300 brides is a six-figure liability. Must exist before the first marketing send |
| 3 | **Review-request flow + Google Business Profile booking link** — extend the existing sold-outcome flow with a review SMS carrying the boutique's GBP review link; set the GBP booking link to the tenant's `/book` page | S | The BridalLive $119/mo add-on, reproduced as one flow on infrastructure that already exists. Reviews are the #1 vendor-selection factor |
| 4 | **Email channel** — install `mass_mailing` (Community), relay through a per-tenant ESP (Brevo/SES free tiers cover boutique volume) with SPF/DKIM/DMARC on the boutique's domain; surface as 2–3 pre-wired flows + a one-screen blast, not the raw builder | M | The missing channel; deliverability is a config problem, not a license problem — but only with the relay |
| 5 | **WhatsApp via Twilio's channel** — extend the existing `sms.py` adapter; utility templates for confirm/remind; start Meta business verification per tenant early, never block launch on it | M | Reuses working credentials and error handling; ~$0.005/message; the 250/day unverified cap already covers a boutique |
| 6 | **Meta lead-ads ingestion** — one webhook controller addon (challenge + signed POST + `page_id`→tenant fan-out → lead/booking invite); begin Meta App Review for `leads_retrieval` immediately; optionally install `crm` as a thin 4-stage pre-booking bucket that converts by *linking* to the partner | M | Proven in-vertical at €3–10/lead; 90-day retention makes automated retrieval mandatory. Skip `crm` if inquiry volume turns out negligible |
| 7 | **`hr_attendance` kiosk** — tablet check-in, monthly worked-hours export for the accountant | S | The one payroll artifact a boutique needs from the system; zero conflict with the custom roster |
| 8 | *(only if flows in 3–5 prove insufficient)* Drip sequencing as a thin `ir.cron` addon over `mailing.trace` | S–M | Replaces the only Enterprise marketing module that matters, in ~100 lines |

### Not doing, on purpose

- **Enterprise licensing** — blocked by settled decision, and the research independently confirms
  it buys nothing needed: marketing gaps close with Community modules + thin addons, staff gaps
  don't exist, and self-hosted Enterprise means the Custom plan per user per tenant plus a partner
  agreement.
- **`social_marketing`** — Enterprise, and post-scheduling is served fine by Meta Business Suite
  (free) outside Odoo.
- **Zapier/Make glue** — premium per-tenant fees plus a documented silent-failure/maintenance-tax
  profile; this repo owns custom addons, so native in-process integration is both lazier and
  sturdier. (Self-hosted n8n is acceptable as a temporary stopgap only.)
- **In-Odoo payroll** — impossible to do compliantly in Israel on any edition; it stays with the
  accountant.
- **Exposing the campaign builder to owners** — abandonment is the top practical risk; defaults
  beat configurability at this scale.
- **Email from the Odoo host** — see finding 4.

---

## Open questions

1. **Exact Israel WhatsApp rates** — the ~$0.035 marketing / ~$0.005 utility figures come from
   third-party rate summaries; pull Meta's official rate card before making pricing promises.
2. **Who owns the Meta Business assets per tenant?** Boutique-owned Business Manager (cleaner
   legally, more onboarding friction) vs MODRYN-owned app with per-tenant pages (one App Review,
   but MODRYN inherits the verification and policy exposure). This is a governance decision that
   shapes item 6.
3. **Is there real pre-booking inquiry volume?** If bookings arrive by phone and are entered
   directly, `crm` is pure ceremony — decide from data after item 6's webhook starts capturing.
4. **Consent capture UX** — where exactly the opt-in checkbox lives (booking form, outcome modal,
   portal) and its he/ar wording is customer-facing copy, i.e. a product decision.
5. Marketing-tool abandonment rates for micro-SMBs are directionally supported but **not** backed
   by a primary dataset — treat finding 9's percentages-free framing as deliberate.

---

## Sources

Credibility: 5 = official docs/source/law · 4 = major firm/vendor primary · 3 = reputable
secondary · ≤2 = flagged, used only with corroboration.

**Odoo editions & modules**
1. [5] `odoo/odoo` GitHub, branch 19.0, `addons/` manifests — presence/absence checks + model source reads (mailing A/B fields, bounce tracking, lead-scoring model, attendance kiosk) — <https://github.com/odoo/odoo/tree/19.0/addons>
2. [5] Odoo editions comparison — <https://www.odoo.com/page/editions> (icon-level HTML parse; an LLM summary of this page initially misread it — repo presence is the authoritative test)
3. [5] Odoo pricing — <https://www.odoo.com/pricing-plan> (Standard $16.90/13.50; Custom $25.50/20.40 per user/mo; Custom required for on-premise)
4. [5] Odoo 19 docs — predictive lead scoring; lead conversion; website form → lead — <https://www.odoo.com/documentation/19.0/applications/sales/crm/track_leads/lead_scoring.html>

**Israeli spam law**
5. [4] Hunton — New Anti-Spam Law Takes Effect in Israel (s.30A regime) — <https://www.hunton.com/privacy-and-information-security-law/new-anti-spam-law-takes-effect-in-israel>
6. [4] Saposhnik law firm — existing-customer exception's three cumulative conditions; ₪1,000/message; officer liability — <https://asandlaw.com/en/sending-unsolicited-commercial-emails-under-israeli-law/>
7. [4] DLA Piper Data Protection Laws — Israel electronic marketing; do-not-call registry — <https://www.dlapiperdataprotection.com/index.html?t=electronic-marketing&c=IL>
8. [3] Lexology — the "main objective" test for transactional vs advertisement — <https://www.lexology.com/library/detail.aspx?g=99eb0abc-6efe-499c-a974-800d4891d2ee>
9. [3] he.wikipedia — חוק הספאם (WhatsApp coverage, Glasberg 2014, amendments; numbering not verified against Reshumot) — <https://he.wikipedia.org/wiki/חוק_הספאם>

**Email deliverability**
10. [5] Odoo 19 docs — email domain / DNS (DKIM required; auth ≠ inbox) — <https://www.odoo.com/documentation/19.0/applications/general/email_communication/email_domain.html>
11. [5] Odoo docs — email servers (separate transactional + mass-mail providers) — <https://www.odoo.com/documentation/16.0/applications/general/email_communication/email_servers.html>
12. [3] dmarcwise / Mailgun — Gmail+Yahoo Feb-2024 sender requirements — <https://dmarcwise.io/blog/gmail-yahoo-new-requirements-2024>

**WhatsApp**
13. [5] Odoo 19 docs — WhatsApp ("Enterprise-only… does not work in Community") — <https://www.odoo.com/documentation/19.0/applications/productivity/whatsapp.html>
14. [5] Meta — WhatsApp Business Platform pricing (per-message since 2025-07-01; free windows; Israel a standalone market) — <https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing>
15. [5] Meta — messaging limits (250/day unverified → tiers) — <https://developers.facebook.com/docs/whatsapp/messaging-limits/>
16. [5] Twilio — WhatsApp pricing ($0.005/msg fee, Meta fees at cost) — <https://www.twilio.com/en-us/whatsapp/pricing>
17. [4] 360dialog pricing (flat €49/mo, zero markup) — <https://360dialog.com/pricing>
18. [3] ChatMaxima — per-country WhatsApp rates incl. Israel (approximate; verify against Meta) — <https://chatmaxima.com/whatsapp-api-pricing/>

**Lead generation & ingestion**
19. [5] Meta — webhooks for leadgen (permissions, App Review, payload) — <https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-leadgen/>
20. [5] Meta — retrieving leads (90-day retention) — <https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving>
21. [5] Google Business Profile Help — local business links (booking link) — <https://support.google.com/business/answer/6218037?hl=en>
22. [5] n8n docs — Facebook Lead Ads trigger (one webhook per app) + Odoo node — <https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.facebookleadadstrigger>
23. [3] Ads-Wind — bridal salon Meta Ads case (2,111 leads, ~$9.79 CPL, €5.75 best) — <https://ads-wind.com/en/cases-en/case-lead-generation-for-a-bridal-dress-salon-in-lisbon-meta-ads/>
24. [4] The Knot 2024 Real Weddings Study (74% follow a vendor on social) — <https://www.theknot.com/content/wedding-data-insights/weddings-in-2024>

**Competitive benchmark**
25. [5] BridalLive — pricing/compare + Smart Flows help docs (all-tier automation; BYO Twilio; $119/mo reputation add-on) — <https://www.bridallive.com/compare>, <https://help.bridallive.com/hc/en-us/articles/360022656452-Smart-Flows>
26. [5] CloudBridal pricing ($80/120/180; SMS automation top-tier only; BYO Twilio) — <https://cloudbridal.com/pricing>
27. [5] Boulevard — text & marketing usage rates ($0.01/email; $2 per campaign-attributed appointment) — <https://support.boulevard.io/en/articles/7042507-text-and-marketing-usage-rates>
28. [4] Fresha help center — marketing toolkit (pay-per-use blasts) — <https://www.fresha.com/help-center/knowledge-base/marketing>

**CRM fit & contrarian**
29. [3] Odoo forum #236750 — lead custom fields don't propagate to partner without `_prepare_customer_values` override — <https://www.odoo.com/forum/help-1/can-not-propagate-custom-attributes-from-crm-lead-to-res-partner-236750>
30. [5] Odoo 19 docs — payroll localizations (Israel absent) — <https://www.odoo.com/documentation/19.0/applications/hr/payroll/payroll_localizations.html>
31. [3] Guberman payroll bureau — Israeli bureau norm (Michpal/Shiklulit/Hilan) — <https://www.guberman.co.il/en/Payroll_Bureau>
32. [4] OCA/payroll — community port, no Israeli rules — <https://github.com/OCA/payroll>
33. [3] Greenme (live Israeli Odoo) — private `l10n_il_payroll_*` import modules prove the journal-import pattern — <https://www.greenme-online.co.il/en/website/info>
34. [3] LowCode/Autonoly — Zapier maintenance tax and silent-failure profile — <https://www.lowcode.agency/blog/zapier-maintenance>

Flagged and discarded: ERPClaw's claim that predictive lead scoring is Enterprise-only
(contradicted by official docs + Community source); a ₪202,000 criminal-compensation figure
(single low-credibility source); Twilio pricing-page line reading "Marketing: no Meta charge"
(parse error); the Odoo Appointments app's edition status (conflicting sources — moot here, the
custom booking engine exists).
