// Act 7 — launch gate 6, automated.
//
// The gate's own stated method is "browser network tab on /shop". This is that,
// with a control, because a page that requested NO fonts at all also makes the
// first assertion pass.
const { test, expect } = require('@playwright/test');

const THIRD_PARTY = /fonts\.(gstatic|googleapis)\.com/;
const SELF_HOSTED = /\/modryn_theme\/static\/src\/fonts\/.*\.woff2/;

for (const path of ['/shop', '/book', '/en/shop', '/ar/shop']) {
  test(`act 7 — ${path} makes no request to Google`, async ({ page }) => {
    const requested = [];
    page.on('request', (r) => requested.push(r.url()));

    await page.goto(path, { waitUntil: 'networkidle' });

    const google = requested.filter((u) => THIRD_PARTY.test(u));
    expect(
      google,
      `requests to Google from ${path}: ${google.join(', ')}. Three mechanisms put ` +
        `them there — the @import compiled into the bundle by website.scss, the ` +
        `unconditional <link rel=preconnect> in website.layout, and the eight base ` +
        `families map-merge used to keep alive for the builder's font picker. ` +
        `Removing only one leaves this failing.`
    ).toHaveLength(0);
  });
}

test('act 7b — the control: our own faces really are being fetched', async ({ page }) => {
  // Without this, act 7 passes just as happily when the fonts never load at all
  // and every page renders in Times New Roman.
  const requested = [];
  page.on('request', (r) => requested.push(r.url()));

  await page.goto('/shop', { waitUntil: 'networkidle' });

  const ours = requested.filter((u) => SELF_HOSTED.test(u));
  expect(ours.length, 'no self-hosted woff2 was requested — the @font-face blocks are absent or the files 404').toBeGreaterThan(0);

  // And the browser actually resolved the family, not just downloaded bytes.
  await expect(page.locator('h1').first()).toBeVisible();
  const loaded = await page.evaluate(() =>
    [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family)
  );
  expect(loaded.join(','), 'no MODRYN family reached status "loaded"').toMatch(/Assistant|Frank Ruhl Libre/);
});

test('act 7c — the Hebrew subset is the one that matters', async ({ page }) => {
  // The hebrew subset carries U+0590-05FF and U+20AA — Hebrew, and the shekel
  // sign the storefront prints on every price. A latin-only build renders the
  // entire catalogue in a fallback face and still looks correct to anyone
  // reviewing in English, which is exactly how it would ship.
  const requested = [];
  page.on('request', (r) => requested.push(r.url()));
  await page.goto('/shop', { waitUntil: 'networkidle' });

  expect(
    requested.filter((u) => /hebrew/.test(u) && SELF_HOSTED.test(u)).length,
    'the Hebrew subset was never requested on a Hebrew storefront'
  ).toBeGreaterThan(0);
});
