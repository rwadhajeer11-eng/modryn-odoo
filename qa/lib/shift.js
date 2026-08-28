// Walking through the floor board's door.
//
// /floor no longer draws the board straight away: a woman starts her shift and
// the room appears, and a screen left open on an empty counter shows an entry
// card rather than the room's live state. Every spec that wants the BOARD has
// to go through that door first — the same two presses a real worker makes.
//
// It is written to be safe to call twice: already on the floor is the state
// these specs want, not an error.
const { expect } = require('@playwright/test');

// She is on the floor when the board is mounted; she is at the door when the
// entry card is. Asserting on the classes rather than on the button's words,
// because those are translated and this suite runs against a Hebrew tenant.
const AT_DOOR = '.modryn_shift_card';
const BOARD = 'owl-component[name="modryn_staff.floor_board"]';

exports.startShift = async (page) => {
  await page.goto('/floor');
  if ((await page.locator(AT_DOOR).count()) === 0) {
    return; // already on the floor
  }
  await Promise.all([
    page.waitForURL(/\/floor/),
    page.locator('form[action="/floor/shift/start"] button[type="submit"]').click(),
  ]);
  await expect(page.locator(BOARD)).toHaveCount(1);
};

// Put her back the way she was found. The flag is stored on hr.employee, so a
// spec that clocks in and never clocks out leaves the tenant with somebody
// permanently on the floor and the NEXT run measures that as the starting
// state — the same trap the roster's window override already taught this suite.
exports.endShift = async (page) => {
  await page.goto('/floor');
  const out = page.locator('form[action="/floor/shift/end"] button[type="submit"]');
  if ((await out.count()) === 0) {
    return; // already off
  }
  await Promise.all([page.waitForURL(/\/floor/), out.click()]);
};
