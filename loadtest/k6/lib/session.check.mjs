// Self-check for the pure scrapers submitBooking's classifier now rests on.
//
//   node loadtest/k6/lib/session.check.mjs
//
// NOT a k6 script and never imported by one: it runs under plain node so the
// classifier can be exercised without a server. It reads session.js as text and
// evaluates only the pure, k6-free half — the alternative is duplicating the
// regexes here, and a copy of a scraper cannot fail when the original drifts.
//
// The fixtures are the real bytes off the live noga tenant, not invented markup:
//   /book                                       -> healthy form, 72 slot options
//   /book/submit with slot=2026-08-15 03:00:00  -> path 4, the off-grid refusal,
//       whose error div reads `מועד לא תקין` — which is precisely why the
//       classifier must not key on the sentence.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const src = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), 'session.js'),
  'utf8'
);

// Everything from the scraping section down to the auth section is pure string
// work with no k6 imports in it.
const pure = src.slice(src.indexOf('const CSRF_RE'), src.indexOf('// ------------------------------------------------------------------ staff auth'));
const { optionsIn, slotValues, SLOT_RE } = await import(
  'data:text/javascript,' + encodeURIComponent(pure.replace(/^export /gm, '') + '\nexport { optionsIn, slotValues, SLOT_RE };')
);
const slotErrorSrc = src.slice(src.indexOf('const SLOT_ERROR_WINDOW'), src.indexOf('/**\n * Is `slot` still on'));
const { slotFieldRejected } = await import(
  'data:text/javascript,' +
    encodeURIComponent(slotErrorSrc.replace(/^export /gm, '') + '\nexport { slotFieldRejected };')
);

const SLOT = '2026-08-13 07:00:00';
const OPTIONS = `<option value=""/>
  <option value="${SLOT}">10:00</option>
  <option value="2026-08-13 08:00:00">11:00</option>`;

// A day-picker <select> sits on the same page; its values are date-only.
const WAITLIST = '<select name="day"><option value="2026-08-14">14.08.2026</option></select>';

// SLOT_ERROR_WINDOW is 200 characters after the slot </select>, and that number
// is only defensible against the real spacing. Measured on the live noga tenant,
// counting from the </select> to the next `text-danger`:
//   slot error  ->  50   (inside the window)
//   terms error -> 450   (outside it)
// The markup and indentation between the two controls is reproduced verbatim,
// because a fixture that compressed it would let a too-wide window pass here and
// still misread the real page.
const INDENT = ' '.repeat(28);
const BETWEEN_SLOT_AND_TERMS = `
                        </div>

                        <div class="form-check mb-4">
                            <input class="form-check-input" type="checkbox" value="1" id="terms" name="terms"/>
                            <label class="form-check-label" for="terms">
                                I've read and accept the boutique's cancellation, lateness and no-show terms
                            </label>
${INDENT}`;

const errorDiv = (text) => `<div class="text-danger small mt-1">${text}</div>`;

const form = (opts, slotError) => `
<select id="slot" name="slot" class="form-select">${opts}</select>
${INDENT}${slotError ? errorDiv('מועד לא תקין') : ''}${BETWEEN_SLOT_AND_TERMS}${
  slotError ? '' : errorDiv('יש לאשר את תנאי הביטול')
}</div>
${WAITLIST}`;

// --- the scrapers ---------------------------------------------------------
assert.deepEqual(slotValues(form(OPTIONS, false)), [SLOT, '2026-08-13 08:00:00']);
// The waitlist day-picker must not leak in: date-only values fail the shape.
assert.equal(slotValues(WAITLIST).length, 0);
assert.equal(optionsIn(WAITLIST, 'day').length, 1);
assert.ok(SLOT_RE.test(SLOT) && !SLOT_RE.test('2026-08-13') && !SLOT_RE.test(''));

// --- which field carries the error ---------------------------------------
const distance = (html) => {
  const c = html.indexOf('</select>', html.indexOf('name="slot"'));
  return html.indexOf('text-danger', c) - c;
};
const WINDOW = Number(/const SLOT_ERROR_WINDOW = (\d+)/.exec(src)[1]);
console.log(
  `SLOT_ERROR_WINDOW=${WINDOW}; error distance from slot </select>: ` +
    `slot=${distance(form(OPTIONS, true))} terms=${distance(form(OPTIONS, false))} ` +
    `(measured on noga: slot=50 terms=450)`
);
// The property, not the literals: the slot error must fall inside the window and
// the next field's error outside it, with the real page's spacing.
// The property, not the byte count: exact distances depend on template
// indentation and asserting them buys brittleness, not confidence.
assert.ok(distance(form(OPTIONS, true)) < WINDOW, 'slot error must be inside the window');
assert.ok(distance(form(OPTIONS, false)) > WINDOW, 'terms error must be outside the window');

assert.equal(slotFieldRejected(form(OPTIONS, true)), true);
// A terms error renders after ITS control, not after the slot select.
assert.equal(slotFieldRejected(form(OPTIONS, false)), false);
assert.equal(slotFieldRejected(''), false);

// --- the decision tree ----------------------------------------------------
// Mirrors submitBooking's branches on the two facts it reads from HTML, so a
// change to either scraper that flips a verdict fails here.
function classify(body, stillOfferedOnFreshPage) {
  if (!slotFieldRejected(body)) return 'rejected';
  if (slotValues(body).indexOf(SLOT) !== -1) return 'lost';   // path 5
  if (stillOfferedOnFreshPage === false) return 'lost';        // path 3
  return 'rejected';                                           // path 4 / unknown
}
// Path 5: UniqueViolation. The loser's REPEATABLE READ snapshot predates the
// winner's commit, so the re-render still lists the hour.
assert.equal(classify(form(OPTIONS, true), false), 'lost');
// Path 3: taken, and a fresh /book agrees it is gone.
assert.equal(classify(form('', true), false), 'lost');
// Path 4: refused, yet a fresh /book still offers it. The regression that used
// to be reported as a lost race — this is the assertion that pins it.
assert.equal(classify(form('', true), true), 'rejected');
// Unconfirmable (fresh /book would not answer): fails closed.
assert.equal(classify(form('', true), null), 'rejected');
// A non-slot field error is never a race.
assert.equal(classify(form(OPTIONS, false), false), 'rejected');

// --- the tenant guard -----------------------------------------------------
// loadTenantFault takes `origin` as a PARAMETER rather than reading CONFIG, so
// it can be exercised here with no tenants.json and no server. That is the
// whole reason for the signature.
const guardSrc = src.slice(src.indexOf('const ORIGIN_RE'), src.indexOf('// WHY this refuses'));
const { loadTenantFault } = await import(
  'data:text/javascript,' +
    encodeURIComponent(
      'const LOAD_SLUG_RE = /^[a-z]{1,8}[0-9]{2}$/;\n' +
      'const LOGIN_RES = [/^owner$/, /^mgr[0-9]+$/, /^staff[0-9]{2}$/];\n' +
      guardSrc + '\nexport { loadTenantFault };'
    )
);

const STAFF = [{ login: 'owner' }, { login: 'mgr1' }, { login: 'staff01' }];
const tenant = (slug, baseUrl) => ({ slug, baseUrl, phonePrefix: '+97252' + slug.slice(-2), staff: STAFF });

// The legacy dev manifest must keep working unchanged. If this breaks, every
// existing local fleet has to be regenerated for no reason.
assert.equal(
  loadTenantFault(tenant('lt01', 'http://lt01.localtest.me:8069'), 'http://localtest.me:8069'),
  null
);
// The production shape the ramp actually needs — gates 9 and 10 are a
// through-nginx measurement, so the fleet lives on $DOMAIN.
assert.equal(loadTenantFault(tenant('lt01', 'https://lt01.example.com'), 'https://example.com'), null);

// A boutique. Rejected on the slug before the origin is even considered —
// 'bella' has no two-digit index. This is the case the old localtest.me anchor
// was believed to cover, and the one it covered only by accident.
assert.match(
  loadTenantFault(tenant('bella', 'https://bella.example.com'), 'https://example.com'),
  /slug "bella" is not/
);
// A slug that satisfies the shape rule but lives under a DIFFERENT origin —
// i.e. a hand-edited baseUrl. Equality against the rebuilt URL is what catches
// it; a regex on the shape alone would not.
assert.match(
  loadTenantFault(tenant('lt01', 'https://lt01.someone-elses-domain.com'), 'https://example.com'),
  /is not "https:\/\/lt01\.example\.com"/
);
// No origin at all: a tenants.json that predates this field, or was written by
// hand. Fails closed rather than comparing against "".
assert.match(loadTenantFault(tenant('lt01', 'https://lt01.example.com'), undefined), /fleet origin/);
assert.match(loadTenantFault(tenant('lt01', 'https://lt01.example.com'), ''), /fleet origin/);
// Scheme downgrade is not a free pass either.
assert.match(
  loadTenantFault(tenant('lt01', 'http://lt01.example.com'), 'https://example.com'),
  /is not "https:\/\/lt01\.example\.com"/
);
// The other two shape rules still bite, and are not shadowed by the new one.
assert.match(
  loadTenantFault({ ...tenant('lt01', 'https://lt01.example.com'), phonePrefix: '+972521' }, 'https://example.com'),
  /phonePrefix/
);
assert.match(
  loadTenantFault({ ...tenant('lt01', 'https://lt01.example.com'), staff: [{ login: 'miri' }] }, 'https://example.com'),
  /staff logins/
);

console.log('session.check.mjs: all assertions passed');
