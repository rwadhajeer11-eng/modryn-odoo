// Submitting the form you actually mean.
//
// THE TRAP, and it cost four red specs the first time. Odoo's `website.layout`
// renders a site-search form into the header of EVERY frontend page — twice,
// once for desktop and once for the mobile offcanvas — and each carries its own
// `<button type="submit">`. Both appear in the DOM *before* the page's real
// form, and both are hidden.
//
// So `page.locator('form button[type=submit]').first()` resolves to a hidden
// search button on every single MODRYN page, and Playwright then spends its
// whole actionability timeout waiting for something that will never be visible.
// The failure message says "element is not visible", which sends you looking at
// the booking form — the one thing that was fine.
//
// Scope by the field instead. `form:has(input[name="slot"])` names the form by
// something only that form has, and reads as what it means.
exports.formWith = (page, fieldName) => page.locator(`form:has([name="${fieldName}"])`);

exports.submitFormWith = async (page, fieldName) => {
  await page
    .locator(`form:has([name="${fieldName}"]) button[type="submit"], form:has([name="${fieldName}"]) input[type="submit"]`)
    .first()
    .click();
};
