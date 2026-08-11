// Read an OTP code back from the staging-only /loadtest/otp endpoint.
//
// There is no other way. modryn.otp.code stores the code as
// HMAC-SHA256(database.secret, "phone|code") — one-way, salted with the
// database secret and bound to the phone — so a psql side channel is
// impossible, and that is a property to keep, not to weaken. Scraping the
// server log would mean every VU regexing one concurrently-appended file.
//
// The endpoint returns the same 404 for every failure (flag off, wrong secret,
// unknown phone, no code), so it is indistinguishable from the addon not being
// installed.

import http from 'k6/http';
import { sleep } from 'k6';

const RETRIES = 3;
const RETRY_SEC = 0.25;

export function fetchOtp(base, phone, secret) {
  const url = `${base}/loadtest/otp?phone=${encodeURIComponent(phone)}&secret=${encodeURIComponent(
    secret || ''
  )}`;
  for (let i = 0; i < RETRIES; i++) {
    // Not tagged with a gated class: this is harness plumbing, not a surface
    // under test, and its latency must not land in the page/form percentiles.
    const res = http.get(url, { tags: { class: 'harness', surface: 'loadtest' } });
    if (res.status === 200) {
      let body;
      try {
        body = res.json();
      } catch (e) {
        return null;
      }
      if (body && body.code) {
        return String(body.code);
      }
    }
    // The row is written inside the request that issued it, so it is already
    // there by the time /my/login returned. The retry costs nothing and covers
    // a cron-drained outbox send that landed a beat late.
    if (i < RETRIES - 1) {
      sleep(RETRY_SEC);
    }
  }
  return null;
}
