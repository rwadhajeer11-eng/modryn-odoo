// Role A — the anonymous storefront. 88% of the workload (spec §4.1).
//
// Sub-split, spec §4.2: 60% browse-and-book, 25% queue check-in, 15% waitlist.

import http from 'k6/http';
import { check, sleep } from 'k6';

import {
  getForm,
  hasMarker,
  myTenant,
  pageTags,
  formTags,
  phoneForVu,
  productLinks,
  slotValues,
  optionsIn,
  submitBooking,
  queueVerify,
} from '../lib/session.js';
import exec from 'k6/execution';

const THINK_MIN = 5;
const THINK_MAX = 15;

function think() {
  sleep(THINK_MIN + Math.random() * (THINK_MAX - THINK_MIN));
}

// Unique per iteration: modryn_check_in de-duplicates on an open phone, so a
// repeated number silently returns the SAME ticket and the walk-in write the
// scenario is meant to exercise never happens.
function iterationPhone(tenant) {
  return phoneForVu(tenant, exec.vu.idInTest * 7 + exec.scenario.iterationInInstance);
}

export default function visitor() {
  const t = myTenant();
  const roll = Math.random();
  if (roll < 0.6) {
    browseAndBook(t);
  } else if (roll < 0.85) {
    queueCheckin(t);
  } else {
    waitlist(t);
  }
}

function browseAndBook(t) {
  const shop = http.get(t.baseUrl + '/shop', { tags: pageTags('storefront') });
  check(shop, { '/shop rendered': (r) => hasMarker(r, 'href="/shop/') }, {
    class: 'page',
    surface: 'storefront',
  });
  think();

  // Real slugs off the page. Catalogues differ per tenant and the slugs are
  // Hebrew, so a synthesised URL is a guaranteed 404 that reads as a fast
  // healthy server.
  const products = productLinks(shop.body);
  let dressId = null;
  if (products.length) {
    const p = products[Math.floor(Math.random() * products.length)];
    dressId = p.id;
    const page = http.get(t.baseUrl + encodeURI(p.href), { tags: pageTags('storefront') });
    check(page, { 'product rendered': (r) => r.status === 200 }, {
      class: 'page',
      surface: 'storefront',
    });
    think();
  } else if (t.dressIds && t.dressIds.length) {
    dressId = t.dressIds[Math.floor(Math.random() * t.dressIds.length)];
  }

  // /book/dress/<id> when we know a dress, /book otherwise. Both call _slots(),
  // which scans every booking in the rendered fortnight — the query this
  // scenario exists to watch grow.
  const bookUrl = dressId ? `${t.baseUrl}/book/dress/${dressId}` : `${t.baseUrl}/book`;
  const form = getForm(bookUrl, 'booking');
  const marked = check(
    form.res,
    { '/book rendered': (r) => hasMarker(r, 'action="/book/submit"') },
    { class: 'page', surface: 'booking' }
  );
  think();

  const slots = slotValues(form.res.body);
  if (!marked || !form.csrf || !slots.length) {
    return; // a fully-booked fortnight is legitimate; nothing to post
  }

  const fields = {
    name: `LoadTest ${t.slug} V${exec.vu.idInTest}`,
    phone: iterationPhone(t),
    csrf_token: form.csrf,
  };
  if (dressId) {
    // A dress without a size is refused server-side ("Please choose a size"),
    // so the variant must come off the same page as the slot.
    const variants = optionsIn(form.res.body, 'variant_id');
    if (!variants.length) {
      return;
    }
    fields.dress_id = String(dressId);
    fields.variant_id = variants[Math.floor(Math.random() * variants.length)];
  }

  const slot = slots[Math.floor(Math.random() * slots.length)];
  const booked = submitBooking(t.baseUrl, slot, fields, 'booking');
  check(booked, { '/book/submit accepted or lost the slot': (b) => b.ok }, {
    class: 'form',
    surface: 'booking',
  });

  if (booked.eventId) {
    think();
    const conf = http.get(`${t.baseUrl}/book/confirmed/${booked.eventId}`, {
      tags: pageTags('booking'),
    });
    check(conf, { '/book/confirmed rendered': (r) => r.status === 200 }, {
      class: 'page',
      surface: 'booking',
    });
  }

  // Distinct render paths and distinct cache keys, not decoration (spec §2.1).
  //
  // In a THROWAWAY jar. Odoo answers /en/... and /ar/... with
  // `Set-Cookie: frontend_lang`, and cookies persist across iterations here
  // (noCookiesReset), so fetching these in the VU's own jar pins that VU to
  // English or Arabic for the whole rest of the run: every later /shop and
  // /book then renders under the /en/ prefix and its Hebrew body marker fails.
  // Measured — it cost /shop and /book about 38% of their checks.
  //
  // A separate jar keeps the alternate renders in the workload (they are the
  // point) while leaving the VU on the Hebrew default it models.
  const lang = Math.random();
  if (lang < 0.3) {
    const altPath = lang < 0.2 ? '/en/shop' : '/ar/shop';
    http.get(t.baseUrl + altPath, { jar: new http.CookieJar(), tags: pageTags('storefront') });
  }
  think();
}

function queueCheckin(t) {
  const form = getForm(t.baseUrl + '/queue/checkin', 'queue');
  check(form.res, { '/queue/checkin rendered': (r) => r.status === 200 }, {
    class: 'page',
    surface: 'queue',
  });
  if (!form.csrf) {
    return;
  }
  think();

  // One number, used by BOTH halves: /queue/verify looks the code up by phone,
  // so a second call to iterationPhone() here would verify against a number
  // that was never sent a code.
  const phone = iterationPhone(t);
  const res = http.post(
    t.baseUrl + '/queue/checkin/submit',
    {
      name: `LoadTest walk-in V${exec.vu.idInTest}`,
      phone: phone,
      client_type: 'bride',
      csrf_token: form.csrf,
    },
    { tags: formTags('queue'), redirects: 0 }
  );
  // NOT "issued a ticket" any more — this 303 goes to /queue/verify and no row
  // exists yet. Renaming it matters: the old label plus the old assertion stayed
  // green through the entire verification change while measuring nothing.
  check(res, { '/queue/checkin/submit issued a code': () => res.status === 303 }, {
    class: 'form',
    surface: 'queue',
  });
  if (res.status !== 303) {
    return;
  }
  think();

  const verified = queueVerify(t.baseUrl, phone);
  check(verified, { '/queue/verify joined the line': (v) => v.ok }, {
    class: 'form',
    surface: 'queue',
  });
  if (!verified.ok) {
    // Do NOT retry. Three issues per phone per hour is the whole budget, and a
    // hot loop here burns it for every VU sharing the number.
    return;
  }
  const ticketPath = verified.location;

  // She watches her place in the queue. A few polls, not a hot loop.
  const polls = 2 + Math.floor(Math.random() * 3);
  for (let i = 0; i < polls; i++) {
    think();
    const q = http.get(t.baseUrl + ticketPath, { tags: pageTags('queue') });
    check(q, { '/q/<token> rendered': (r) => r.status === 200 }, {
      class: 'page',
      surface: 'queue',
    });
  }
}

function waitlist(t) {
  const form = getForm(t.baseUrl + '/book', 'booking');
  if (!form.csrf) {
    return;
  }
  // The waitlist box only renders when at least one day in the fortnight is
  // full — on a fresh tenant there is nothing to join, and that is not a fault.
  const days = optionsIn(form.res.body, 'day');
  if (!days.length) {
    return;
  }
  think();

  const res = http.post(
    t.baseUrl + '/waitlist/join',
    {
      day: days[Math.floor(Math.random() * days.length)],
      name: `LoadTest waitlist V${exec.vu.idInTest}`,
      phone: iterationPhone(t),
      csrf_token: form.csrf,
    },
    { tags: formTags('booking'), redirects: 0 }
  );
  check(res, { '/waitlist/join accepted': (r) => r.status === 303 }, {
    class: 'form',
    surface: 'booking',
  });
  if (res.status === 303) {
    const done = http.get(t.baseUrl + res.headers['Location'], { tags: pageTags('booking') });
    check(done, { '/waitlist/done rendered': (r) => r.status === 200 }, {
      class: 'page',
      surface: 'booking',
    });
  }
  think();
}
