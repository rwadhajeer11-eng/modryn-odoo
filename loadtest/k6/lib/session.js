// Cookie jar, CSRF, tenant selection, staff sign-in, customer OTP sign-in.
//
// Everything in here exists because one of these facts bit somebody:
//   - a failed /staff/login answers 200, not 4xx
//   - signing in HARD-rotates the sid, so a pre-login CSRF token is dead after
//   - /book/submit now refuses any timestamp the server did not itself offer
//   - a live boutique must never be a target: see guardTenants() for what the
//     harm actually is (it is NOT that the seeded numbers reach handsets)

import http from 'k6/http';
import { fail } from 'k6';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';

import { fetchOtp } from './otp.js';

const CONFIG = JSON.parse(open('../../config/tenants.json'));

// --------------------------------------------------------- default headers
//
// Odoo's website layer negotiates a language per request and 303-redirects to a
// prefixed URL (/en/shop) for any language that is not the tenant default. These
// tenants default to he_IL, so a client that does not ask for Hebrew is served
// the ENGLISH site — different URLs, different body markers, and a booking form
// posting to /en/book/submit. Every page marker in this harness failed for that
// reason, silently, while http_req_failed stayed at 0.00%.
//
// It was invisible during development because the scrapers were verified with
// curl, and odoo/addons/web/models/ir_http.py:30 lists "curl" in `bots` —
// is_a_bot() short-circuits the language negotiation, so curl sees the Hebrew
// default and k6 does not. Verifying a scraper with a different client than the
// one that runs it is what hid this.
//
// Do NOT "fix" this by putting curl/bot in the User-Agent: taking the bot path
// also skips _register_website_track, so the server would stop doing the
// website.visitor work every real browser makes it do, and the load would be
// understated. Ask for Hebrew like a real Israeli browser instead.
const DEFAULT_HEADERS = { 'Accept-Language': 'he-IL,he;q=0.9,en;q=0.5' };

// Patched once, on the shared k6 http module object, rather than at each of the
// ~30 call sites.
//
// COVERAGE, precisely: http.get, http.post and http.request. Those are the only
// three the harness uses, and k6 exposes each verb as its own binding rather
// than routing them through http.request, so patching one does NOT cover the
// others — http.del/put/patch/head/options and http.batch are NOT patched and a
// call site added with one of them would silently be served the English site.
// Reach for get/post/request, or extend this block in the same commit.
if (!http.__modrynDefaultHeaders) {
  const rawGet = http.get;
  const rawPost = http.post;
  const rawRequest = http.request;
  const withDefaults = (params) => {
    const p = Object.assign({}, params);
    // Caller-supplied headers win, so a scenario can still override one.
    p.headers = Object.assign({}, DEFAULT_HEADERS, p.headers || {});
    return p;
  };
  http.get = function (url, params) {
    return rawGet(url, withDefaults(params));
  };
  http.post = function (url, body, params) {
    return rawPost(url, body, withDefaults(params));
  };
  http.request = function (method, url, body, params) {
    return rawRequest(method, url, body, withDefaults(params));
  };
  http.__modrynDefaultHeaders = true;
}

// The one guard that is not about measurement. A tenant with the four
// modryn.twilio.* parameters set makes modryn.sms._send_now take the Twilio
// branch, so every booking, waitlist offer and OTP is a real SMS to a real
// Israeli mobile. bella has them; deploy/scripts/new_boutique_prod.sh sets them
// on every production boutique it provisions.
//
// This is an ALLOWLIST and it FAILS CLOSED. The previous version refused any
// tenant whose slug or URL contained "bella" — a denylist, which passed noga and
// every production boutique not literally named bella, i.e. exactly the tenants
// that do have credentials. A name is not a capability.
//
// k6 cannot read ir_config_parameter, so the property actually checked here is
// "this manifest entry is one loadtest/seed/gen_tenants.sh wrote", and that
// script has its own per-tenant gate that hard-refuses any tenant with a
// modryn.twilio.* row (gen_tenants.sh §THE GATE). The rules below are its output
// format, verbatim:
//
//   slug         "<prefix><NN>", prefix from --prefix (default lt), NN = %02d
//                in 01..99 — gen_tenants.sh:69-70 with the 01..99 bound at :47
//   baseUrl      "http://<slug>.localtest.me:<port>"          — gen_tenants.sh:170
//   phonePrefix  "+97252<NN>", NN the slug's own last two      — gen_tenants.sh:174
//   staff        owner + mgr1..mgrM + staff01..staffS          — gen_tenants.sh:155-157
//   loadtestSecret at the top level                            — gen_tenants.sh:178
//
// It writes NO prefix field and NO load-tenant marker, so there is nothing
// single to require; the shape as a whole is the marker. If gen_tenants.sh ever
// gains one, require that instead and delete the rest of this.
//
// bella and noga fail on the slug rule (no two-digit index). A production
// boutique fails on baseUrl and phonePrefix — new_boutique_prod.sh serves it at
// https://<slug>.$DOMAIN, never on localtest.me.
const LOAD_SLUG_RE = /^[a-z]{1,8}[0-9]{2}$/;
const LOGIN_RES = [/^owner$/, /^mgr[0-9]+$/, /^staff[0-9]{2}$/];

// ORIGIN_RE bounds what may be claimed as the fleet origin at all. Without it
// an origin of "" would make every baseUrl of the form "<scheme>://<slug>."
// compare equal to itself and the check below would be vacuous.
const ORIGIN_RE = /^https?:\/\/[a-z0-9.-]+(:[0-9]+)?$/;

// `origin` is a PARAMETER, not a CONFIG read, so this function stays pure and
// session.check.mjs can exercise it under plain node with no server and no
// tenants.json. guardTenants() below passes CONFIG.origin.
function loadTenantFault(t, origin) {
  const slug = String(t.slug || '');
  if (!LOAD_SLUG_RE.test(slug)) {
    return `slug "${slug}" is not <prefix><two-digit index>`;
  }
  // The dev anchor "localtest.me" was carrying TWO arguments at once, and only
  // one of them survives being pointed at production. It proved (a) that
  // gen_tenants.sh wrote this file, and (b) that the target cannot be a
  // boutique — because new_boutique_prod.sh serves every boutique at
  // https://<slug>.$DOMAIN and never on localtest.me.
  //
  // Launch gates 9 and 10 need a THROUGH-NGINX measurement, so the load fleet
  // has to live on $DOMAIN and (b) is gone. No shape rule brings it back:
  // 'lt01' is a perfectly legal boutique slug under new_boutique_prod.sh's own
  // grammar. What replaces (b) is a CAPABILITY probe — /loadtest/ping, called
  // for every tenant from setup() in main.js before a single VU runs. A
  // boutique cannot answer it, because loadtest/odoo_addons is not on
  // production's addons_path and the module is not even discoverable there.
  //
  // (a) is kept and tightened. The URL is not pattern-matched, it is RECOMPUTED
  // from the slug and the one origin the manifest carries for the whole fleet,
  // and compared for equality. A hand-edited baseUrl cannot pass without also
  // being exactly <slug>.<origin> for a slug that already satisfies the shape
  // rule and a phonePrefix that already satisfies +97252<NN>.
  const o = String(origin || '');
  if (!ORIGIN_RE.test(o)) {
    return `fleet origin "${o}" is not <scheme>://<host>[:port] — tenants.json was not written by gen_tenants.sh`;
  }
  const sep = o.indexOf('://');
  const expected = `${o.slice(0, sep)}://${slug}.${o.slice(sep + 3)}`;
  const url = String(t.baseUrl || '');
  if (url !== expected) {
    return `baseUrl "${url}" is not "${expected}" (rebuilt from the fleet origin)`;
  }
  const expectedPrefix = '+97252' + slug.slice(-2);
  if (String(t.phonePrefix || '') !== expectedPrefix) {
    return `phonePrefix "${t.phonePrefix}" is not "${expectedPrefix}"`;
  }
  const logins = (t.staff || []).map((s) => String(s.login || ''));
  const stray = logins.find((l) => !LOGIN_RES.some((re) => re.test(l)));
  if (!logins.length || stray !== undefined) {
    return `staff logins are not the seeded owner/mgrN/staffNN set (saw "${stray}")`;
  }
  return null;
}

// WHY this refuses, stated accurately — earlier comments here claimed the seeded
// numbers would "text real Israeli mobiles", and that is FALSE: '+97252' + 2-digit
// tenant + 4-digit VU is 11 digits, while an Israeli mobile in E.164 is 12, so
// Twilio answers 21211 invalid-To. reset_tenants.sh's own SQL agrees — it filters
// length(phone) = 12. Overstating the harm is how a reviewer talks themselves into
// leaving a weak guard alone, so here is what running against a live boutique
// really costs:
//   1. It writes hundreds of fake bookings, walk-ins and waitlist rows into a
//      working shop. Staff see invented customers on the floor board mid-service,
//      and every one of them is indistinguishable from a real arrival.
//   2. It burns the boutique's Twilio quota and error budget on rejected sends.
//   3. The seeded numbers are one "make the fixtures realistic" edit away from
//      being deliverable, and nothing outside this file pins the digit count.
// Point 1 alone justifies failing closed. The allowlist above refuses anything
// that is not recognisably a generated load tenant, so a boutique that is simply
// not named 'bella' — which a name denylist happily accepted — cannot get through.
export function guardTenants() {
  if (!CONFIG.tenants || !CONFIG.tenants.length) {
    fail('refusing to run: loadtest/config/tenants.json lists no tenants');
  }
  if (!CONFIG.loadtestSecret) {
    fail(
      'refusing to run: loadtest/config/tenants.json has no loadtestSecret, so it ' +
        'was not written by loadtest/seed/gen_tenants.sh'
    );
  }
  // From the FILE, never __ENV. The file is the artifact this gate inspects,
  // and an environment variable would let an operator retarget the harness at
  // runtime while the gate went on inspecting something else.
  for (const t of CONFIG.tenants) {
    const fault = loadTenantFault(t, CONFIG.origin);
    if (fault) {
      fail(
        `refusing to run: tenant "${t.slug}" does not match what ` +
          `loadtest/seed/gen_tenants.sh writes for a load tenant — ${fault}. ` +
          `Only generated load tenants are allowed here. Regenerate ` +
          `tenants.json with gen_tenants.sh.`
      );
    }
  }
}

// THE ONLY CHECK THAT IS A CAPABILITY AND NOT A NAME.
//
// Everything guardTenants() asserts is shape, and shape can be forged by
// anyone who can write tenants.json. This asks each target to prove it is
// running the staging capture addon — a property a production boutique
// provably cannot have, because loadtest/odoo_addons is not on production's
// addons_path and the module is not discoverable there at all.
//
// Called from setup() in main.js, which k6 runs ONCE, before any VU exists, so
// a mis-shaped fleet dies before the first invented customer is written.
// Every tenant, never a sample: a fleet of 29 load tenants and one boutique is
// exactly the accident this exists to stop.
export function guardTenantsAreLoadTargets(http, secret) {
  for (const t of TENANTS) {
    const r = http.get(`${t.baseUrl}/loadtest/ping?secret=${encodeURIComponent(secret)}`);
    if (r.status !== 200) {
      fail(
        `refusing to run: ${t.baseUrl} answered ${r.status} to /loadtest/ping, not 200. ` +
          `It is not running the staging capture addon, so it is NOT a load tenant — ` +
          `and the shape checks above cannot tell a boutique named "lt01" from a ` +
          `generated one. Nothing has been written to any tenant.`
      );
    }
  }
}

export const TENANTS = CONFIG.tenants;
export const LOADTEST_SECRET = __ENV.LOADTEST_SECRET || CONFIG.loadtestSecret;

// The seeded password is never in this repo — seed_staff.py deliberately raises
// when MODRYN_DEMO_PASSWORD is unset, so a default here would re-commit the
// credential it removed.
export function staffPassword() {
  const pw = __ENV.MODRYN_DEMO_PASSWORD;
  if (!pw) {
    fail('MODRYN_DEMO_PASSWORD is unset — export the password the tenants were seeded with');
  }
  return pw;
}

// Sticky per VU for the whole run. A VU that browses lt07's shop must book on
// lt07: the session cookie is bound to the database the Host header selected,
// so switching hosts mid-session produces CSRF 400s that have nothing to do
// with load (spec §5).
export function myTenant() {
  return TENANTS[(exec.vu.idInTest - 1) % TENANTS.length];
}

/**
 * Index by the VU's ordinal WITHIN its tenant, not by its global id.
 *
 * myTenant() hands out tenants round-robin, so every VU on one tenant is
 * congruent mod TENANTS.length. Indexing the staff pool with idInTest directly
 * therefore walked the subgroup generated by gcd(tenants, pool) and could never
 * touch more than pool/gcd of the logins however many VUs ran: at the campaign's
 * 30 tenants that ceiling is 2 of 12 staff and 1 of 3 managers. Hitting the same
 * two logins keeps their per-uid access-rights caches unrealistically hot, which
 * understates load, while concentrating every roster write on one employee's
 * rows, which invents lock contention that is not there.
 *
 * Dividing the stride out first gives consecutive VUs on a tenant consecutive
 * ordinals, so no shared factor can reintroduce a subgroup for any tenant count.
 *
 * `stride` is how far apart in idInTest two VUs on the SAME tenant are. It
 * defaults to myTenant()'s round-robin, and must be passed as 1 by any entry
 * point that pins every VU to one tenant (focused/bus_storm.js) — there the
 * stride is not TENANTS.length, and assuming it would shrink coverage instead
 * of widening it.
 */
export function staffOfLevel(tenant, level, stride) {
  const pool = tenant.staff.filter((s) => s.level === level);
  if (!pool.length) {
    fail(`tenant ${tenant.slug} seeded no staff at level "${level}"`);
  }
  const ordinalInTenant = Math.floor((exec.vu.idInTest - 1) / (stride || TENANTS.length));
  return pool[ordinalInTenant % pool.length];
}

// Deterministic per VU, per tenant. modryn.otp.code allows 3 issues per phone
// per rolling hour counted on rows created, so a restarted stage that reused
// random numbers would collide with the previous stage's rows and every
// customer login would come back rate_limited (plan §3).
export function phoneForVu(tenant, vu) {
  return tenant.phonePrefix + String(vu % 10000).padStart(4, '0');
}

// Every iteration of every scenario calls this first. Gates in config/
// thresholds.js apply to phase:steady only.
export function tagPhase(stage) {
  const elapsedSec = exec.instance.currentTestRunDuration / 1000;
  if (elapsedSec < stage.rampUpSec) {
    exec.vu.tags.phase = 'rampup';
  } else if (elapsedSec < stage.rampUpSec + stage.steadySec) {
    exec.vu.tags.phase = 'steady';
  } else {
    exec.vu.tags.phase = 'rampdown';
  }
}

// ------------------------------------------------------------------- scraping

// The token is HMAC-SHA1(database.secret, sid[:42] || max_ts) and is bound to
// the session that served the page. Every form on a page carries the same
// value, so a page-wide scrape is unambiguous — verified against the real
// /book HTML, which renders it three times with one value.
const CSRF_RE = /name="csrf_token" value="([^"]*)"/;

export function csrfFrom(body) {
  const m = CSRF_RE.exec(body || '');
  if (!m || !m[1]) {
    // A blank token silently becomes a 400 several requests later, by which
    // point the cause looks like a routing fault. Die here instead.
    return null;
  }
  return m[1];
}

// Scope option scraping to one <select>. /book renders TWO selects whose
// options both look like `<option value="...">`: slot (datetime) and the
// waitlist day (date). An unscoped scrape mixes them.
export function optionsIn(body, selectName) {
  const block = new RegExp(`<select[^>]*name="${selectName}"[^>]*>([\\s\\S]*?)</select>`).exec(
    body || ''
  );
  if (!block) {
    return [];
  }
  const out = [];
  const re = /<option[^>]*\svalue="([^"]*)"/g;
  let m;
  while ((m = re.exec(block[1])) !== null) {
    if (m[1]) {
      out.push(m[1]); // the placeholder <option value=""> is not a choice
    }
  }
  return out;
}

// Both /book/submit and /claim now reject any timestamp the server would not
// itself have offered, so the harness must post a value it read off the page.
// Shape check as well as scoping: `%Y-%m-%d %H:%M:%S`, which the date-only
// waitlist options cannot satisfy even if the scoping ever regressed.
const SLOT_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

export function slotValues(body) {
  return optionsIn(body, 'slot').filter((v) => SLOT_RE.test(v));
}

// Product hrefs are `/shop/<hebrew-slug>-<id>`. Excludes /shop/cart,
// /shop/wishlist and friends by requiring the trailing -<digits>.
const PRODUCT_RE = /href="(\/shop\/[^"]*?-(\d+))"/g;

export function productLinks(body) {
  const out = [];
  let m;
  PRODUCT_RE.lastIndex = 0;
  while ((m = PRODUCT_RE.exec(body || '')) !== null) {
    out.push({ href: m[1], id: parseInt(m[2], 10) });
  }
  return out;
}

// ------------------------------------------------------------------- requests

export function pageTags(surface) {
  return { class: 'page', surface: surface };
}
export function formTags(surface) {
  return { class: 'form', surface: surface };
}

// A styled 200 error page is still a 200. Every HTML render is checked for a
// marker that only appears when the page really rendered (spec §7.1.2).
export function hasMarker(res, marker) {
  return res.status === 200 && String(res.body || '').includes(marker);
}

// Login pages this app can land you on. Reaching one is the signal that the
// request was NOT authenticated.
const LOGIN_URL_RE = /\/(my|staff|web)\/login(\?|$)/;

/**
 * A page that only exists for a signed-in user.
 *
 * `res.status === 200` is NOT a check on these routes. An unauthenticated
 * request to /manage/staff answers 303 to /web/login, k6 follows it, and the
 * login page renders a perfectly good HTTP 200 — so a status-only check reports
 * every logged-out iteration as a healthy render. Measured: /manage/staff,
 * /manage/roles, /manage/rooms, /manage/pieces, /manage/shifts and /roster all
 * scored 100% while their VU had no session at all.
 *
 * So: 200, did not land on a login page, and (where one exists) carries a
 * marker that the login page cannot contain.
 */
export function authedPage(res, marker) {
  if (res.status !== 200 || LOGIN_URL_RE.test(String(res.url || ''))) {
    return false;
  }
  return marker ? String(res.body || '').includes(marker) : true;
}

// GET a form page and return {res, csrf}. Params allow a private jar.
export function getForm(url, surface, params) {
  const res = http.get(url, Object.assign({ tags: pageTags(surface) }, params || {}));
  return { res: res, csrf: csrfFrom(res.body) };
}

// ------------------------------------------------------------------ staff auth

// Ported from scripts/verify.sh §10a, which is the canonical working sequence.
export function staffLogin(base, username, password, params) {
  const url = base + '/staff/login';
  // The GET is not optional: the handler calls request.session.touch(), and
  // without it Odoo emits no session cookie at all for a visitor whose first
  // request is the login page — the POST then lands under a brand-new sid and
  // is rejected with a bare 400.
  const form = getForm(url, 'staff', params);
  if (!form.csrf) {
    return { ok: false, reason: 'no_csrf' };
  }
  const res = http.post(
    url,
    { username: username, password: password, csrf_token: form.csrf },
    Object.assign({ tags: formTags('staff'), redirects: 0 }, params || {})
  );
  // Success is 303. Failure RE-RENDERS the login template as HTTP 200, so a
  // status===200 check inverts pass and fail (plan §2, landmine 7).
  return { ok: res.status === 303, status: res.status, landing: res.headers['Location'], res: res };
}

// Any type='http' POST made after this must scrape a FRESH token: finalize()
// sets should_rotate and _save_session rotates hard (soft=False), regenerating
// the whole sid. Every token scraped before login is dead.

// --------------------------------------------------------------- customer auth

// Runs AT MOST ONCE per VU per run. modryn.otp.code allows 3 issues per phone
// per rolling hour; a 30-minute stage plus a soak sits inside one hour for most
// of its length, so a second login is an incident, not a retry budget.
export function customerLogin(base, phone) {
  const loginUrl = base + '/my/login';
  const form = getForm(loginUrl, 'portal');
  if (!form.csrf) {
    return { ok: false, reason: 'no_csrf' };
  }
  const post = http.post(
    loginUrl,
    { phone: phone, csrf_token: form.csrf },
    { tags: formTags('portal'), redirects: 0 }
  );
  if (post.status !== 303) {
    // 200 here is the re-rendered login page: rate_limited or invalid_number.
    // Both are application-level refusals, not errors — the caller decides.
    return { ok: false, reason: 'issue_refused', status: post.status };
  }

  const code = fetchOtp(base, phone, LOADTEST_SECRET);
  if (!code) {
    return { ok: false, reason: 'no_code' };
  }

  const verifyUrl = base + '/my/verify';
  const vform = getForm(verifyUrl, 'portal');
  if (!vform.csrf) {
    return { ok: false, reason: 'no_csrf' };
  }
  const verify = http.post(
    verifyUrl,
    { code: code, csrf_token: vform.csrf },
    { tags: formTags('portal'), redirects: 0 }
  );
  return { ok: verify.status === 303, reason: 'verify', status: verify.status };
}

// ------------------------------------------------------------------- booking

export const bookingCreated = new Counter('booking_created');
export const bookingLostRace = new Counter('booking_lost_race');
export const bookingRejected = new Counter('booking_rejected');

// Did the re-rendered form put an error on the SLOT field specifically?
//
// templates.xml renders every field error the same way — a
// `<div class="text-danger small mt-1">` immediately after that field's own
// control — so the field is identified by POSITION, not by the sentence inside
// it. That matters: he_IL is the default language, so the English strings in
// main.py are not what ships, and a message match would be a per-locale trap.
//
// Anchored to the slot <select>'s closing tag. A name, phone, variant or terms
// error renders after ITS control and is correctly not seen here.
const SLOT_ERROR_WINDOW = 200;

export function slotFieldRejected(body) {
  const html = String(body || '');
  const sel = html.indexOf('name="slot"');
  if (sel === -1) {
    return false;
  }
  const close = html.indexOf('</select>', sel);
  if (close === -1) {
    return false;
  }
  return html.slice(close, close + SLOT_ERROR_WINDOW).includes('text-danger');
}

/**
 * Is `slot` still on the server's own offer list, asked fresh?
 *
 * Tagged class:harness — this is the classifier's instrument, not a surface
 * under test, and its latency must not land in the page percentiles (the same
 * reason lib/otp.js tags its reads that way).
 */
function stillOffered(base, slot) {
  const res = http.get(base + '/book', { tags: { class: 'harness', surface: 'booking' } });
  if (res.status !== 200) {
    return null; // cannot tell; the caller fails closed
  }
  const offered = slotValues(res.body);
  if (!offered.length) {
    return null; // an empty grid answers nothing about this one slot
  }
  return offered.indexOf(slot) !== -1;
}

/**
 * POST /book/submit with a slot value scraped off the page that offered it.
 *
 * Never synthesise the timestamp. The controller now asks _slots() whether the
 * posted hour is one it would itself have offered, so an arbitrary datetime is
 * refused outright — a crafted POST used to book 03:00 on a closed Saturday.
 *
 * THE FIVE SLOT-ERROR PATHS in addons/modryn_booking/controllers/main.py, and
 * what each one means:
 *
 *   1. :199  no slot posted at all               harness fault
 *   2. :204  slot fails strptime                 harness fault
 *   3. :229  pre-check found a live booking      genuine contention loss
 *   4. :245  slot is not in _slots()             the server refuses an hour it
 *                                                offered — a regression
 *   5. :298  UniqueViolation on the partial index  genuine contention loss
 *
 * All five re-render the same form with the same positional error div, and all
 * five used to be reported as {ok:true, lost:true} — a success, outside the
 * error budget. So a slot-list regression under load would have made every POST
 * a "lost race", booking_created 0, and every threshold green.
 *
 * Discriminating them WITHOUT matching prose, because he_IL is the default
 * language and the English strings in main.py are not what ships. Probed
 * against the live noga tenant: an off-grid slot (path 4) renders exactly
 * `<div class="text-danger small mt-1">מועד לא תקין</div>`, so any classifier
 * keyed on the sentence is a per-locale trap.
 *
 *   1,2  pre-empted below, before the request: the value we are about to post
 *        must be a non-empty `%Y-%m-%d %H:%M:%S`. Neither path can then be
 *        reached, and a harness bug is caught where it happened.
 *   5    the losing racer's snapshot predates the winner's commit — Odoo runs
 *        REPEATABLE READ (odoo/odoo/sql_db.py:373) — so _render_form's second
 *        _slots() STILL lists our hour. Measured on a 50-VU single-slot race:
 *        1 winner, 49 losers, 49 re-renders still offering the slot. Our slot
 *        present in the re-render is therefore path 5, decisively.
 *   3,4  both re-render WITHOUT our slot, so the response alone cannot separate
 *        them. Ask the server instead: re-GET /book and see whether it still
 *        offers the hour it just refused.
 *          still offered -> it contradicts itself   -> path 4, a REJECTION
 *          gone          -> somebody committed on it -> path 3, a lost race
 *        Robust because it uses the server's own grid as the oracle rather than
 *        restating the boutique's opening hours/DAYS_AHEAD/DST here, where they would
 *        drift from _slots(). It costs one extra GET per loss, which is why it
 *        runs only on the ambiguous branch.
 *
 * Residual ambiguity, stated rather than hidden: a cancellation landing between
 * the POST and the re-GET makes a genuine loss read as a rejection. That is the
 * safe direction — it over-reports failure — and it is rare.
 */
export function submitBooking(base, slot, fields, surface) {
  // Paths 1 and 2, caught before they can be mistaken for a lost race.
  if (!SLOT_RE.test(String(slot || ''))) {
    bookingRejected.add(1);
    return { ok: false, lost: false, eventId: null, reason: 'bad_slot_value', res: null };
  }

  const body = Object.assign({ slot: slot, terms: 'on' }, fields);
  const res = http.post(base + '/book/submit', body, {
    tags: formTags(surface || 'booking'),
    redirects: 0,
  });

  if (res.status === 303) {
    bookingCreated.add(1);
    const loc = res.headers['Location'] || '';
    const m = /\/book\/confirmed\/(\d+)/.exec(loc);
    return { ok: true, lost: false, eventId: m ? parseInt(m[1], 10) : null, res: res };
  }

  if (res.status === 200 && slotFieldRejected(res.body)) {
    const lost = function (why) {
      bookingLostRace.add(1);
      return { ok: true, lost: true, eventId: null, reason: why, res: res };
    };
    if (slotValues(res.body).indexOf(slot) !== -1) {
      return lost('unique_index'); // path 5
    }
    const offered = stillOffered(base, slot);
    if (offered === false) {
      return lost('slot_taken'); // path 3
    }
    // offered === true is path 4. offered === null means /book would not answer
    // or offered nothing at all — either way the slot list is not healthy and we
    // cannot call this a race we lost, so it fails closed as a rejection.
    bookingRejected.add(1);
    return {
      ok: false,
      lost: false,
      eventId: null,
      reason: offered ? 'slot_offered_then_refused' : 'slot_list_unhealthy',
      res: res,
    };
  }

  bookingRejected.add(1);
  return { ok: false, lost: false, eventId: null, reason: 'form_rejected', res: res };
}

// The websocket needs the session cookie explicitly: k6's ws module does not
// share the HTTP module's jar.
export function sessionCookieHeader(base) {
  const jar = http.cookieJar();
  const cookies = jar.cookiesForURL(base);
  const sid = cookies['session_id'];
  return sid && sid.length ? `session_id=${sid[0]}` : '';
}
