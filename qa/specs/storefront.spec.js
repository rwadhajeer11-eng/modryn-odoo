// Acts 1 and 2: the storefront renders, in both writing directions.
//
// Everything here is invisible to curl. verify.sh asserts that /shop returns
// 200 and that the gold hex and the display font appear in the CSS bundle —
// both true of a stylesheet the browser never successfully applied.
const { test, expect } = require('@playwright/test');

const GOLD = 'rgb(197, 160, 89)';   // #C5A059
const INK = 'rgb(43, 33, 24)';      // #2B2118

test('act 1 — Hebrew storefront is RTL, and the palette actually applied', async ({ page }) => {
  await page.goto('/shop');

  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', /^he/);

  // THE POINT OF THIS WHOLE FILE. LibSass predates CSS Color Level 4 and dies
  // on `rgb(a b c / d)` — silently, taking the ENTIRE frontend bundle rather
  // than the offending rule. primary_variables.scss:24 and modryn.scss both
  // carry that warning because it has already happened once. curl sees a 200
  // with correct HTML and no styling at all; only a browser can tell you the
  // computed value.
  const h1 = page.locator('h1').first();
  await expect(h1).toBeVisible();
  const headingFont = await h1.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(headingFont).toMatch(/Frank Ruhl Libre/);

  const body = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  expect(body).toMatch(/Assistant/);
});

test('act 1b — the gold is the MODRYN gold, and it carries readable text', async ({ page }) => {
  await page.goto('/shop');

  // Any element that ended up gold. Asserting on the token rather than on a
  // particular button, because which element is primary is a design decision
  // and the palette is not.
  const goldCount = await page.evaluate((gold) => {
    return [...document.querySelectorAll('a,button,.btn,span,div')]
      .filter((el) => getComputedStyle(el).backgroundColor === gold).length;
  }, GOLD);
  expect(goldCount, 'no element renders in #C5A059 — the palette did not reach the page').toBeGreaterThan(0);
});

test('act 1c — no horizontal scroll on a phone', async ({ page }) => {
  // RTL layouts overflow in the direction nobody tests. 375px is the iPhone SE
  // width the design process rules name as the mobile breakpoint.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/shop');
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow, 'the page scrolls sideways at 375px').toBeLessThanOrEqual(1);
});

test('act 2 — Arabic renders from a different bundle and is still RTL', async ({ page }) => {
  // /he/ compiles to web.assets_frontend.rtl.min.css and /en/ to
  // web.assets_frontend.min.css — measured at 995,393 and 994,804 bytes. They
  // are DIFFERENT FILES: a theme change can compile in one and break in the
  // other, and checking only the default language would never see it.
  const errors = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

  await page.goto('/ar/shop');
  await expect(page.locator('html')).toHaveAttribute('lang', /^ar/);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');

  const h1 = page.locator('h1').first();
  await expect(h1).toBeVisible();
  expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toMatch(/Frank Ruhl Libre/);

  expect(errors, `console errors on /ar/shop: ${errors.join(' | ')}`).toHaveLength(0);
});

test('act 2b — the LTR bundle compiles too', async ({ page }) => {
  await page.goto('/en/shop');
  await expect(page.locator('html')).toHaveAttribute('lang', /^en/);
  const h1 = page.locator('h1').first();
  await expect(h1).toBeVisible();
  expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toMatch(/Frank Ruhl Libre/);
  expect(await page.evaluate(() => getComputedStyle(document.body).color)).toBe(INK);
});
