// ONE suite, two targets. BASE_URL is the whole parameterisation.
//
//   dev   BASE_URL=http://bella.localtest.me:8069        npm test
//   prod  BASE_URL=https://qa.example.com QA_TENANTS=qa  npm run test:prod
//
// The projects differ ONLY in which tests they are allowed to run. Every spec
// that creates a row is tagged @writes, and the prod project excludes them
// unless QA_ALLOW_WRITES is set — see lib/guard.js for what that gate checks
// before it lets them through.
const { defineConfig, devices } = require('@playwright/test');

const BASE_URL = process.env.BASE_URL || 'http://bella.localtest.me:8069';

module.exports = defineConfig({
  testDir: './specs',
  globalSetup: require.resolve('./lib/guard.js'),

  // SERIAL, and not for performance. The booking spec takes a real slot out of
  // a shared grid, and the queue spec asserts on a board that every other
  // test's writes also land on. Parallel workers would make both flaky in a way
  // that reads as a product bug — which is the most expensive kind of flake,
  // because someone goes looking in the product.
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    // he_IL is the tenant default, and Odoo 303s to /en/... for anything that
    // does not ask for Hebrew — different URLs, different markup, and a
    // different <form action> rendered into the page. loadtest/k6/lib/session.js
    // carries the full account of how that silently broke every page marker in
    // the k6 harness. It does not need repeating; it needs obeying.
    locale: 'he-IL',
    extraHTTPHeaders: { 'Accept-Language': 'he-IL,he;q=0.9,en;q=0.5' },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 15000,
    navigationTimeout: 30000,
    // Production serves a real certificate; dev is plain http. Never true — a
    // suite that ignores certificate errors cannot notice the self-signed
    // placeholder provision.sh installs at bootstrap.
    ignoreHTTPSErrors: false,
  },

  projects: [
    { name: 'dev', use: { ...devices['Desktop Chrome'] } },
    {
      name: 'prod',
      use: { ...devices['Desktop Chrome'] },
      // A production run is READ-ONLY unless you say otherwise, and even then
      // guard.js refuses any tenant that could text a real person.
      grepInvert: process.env.QA_ALLOW_WRITES ? undefined : /@writes/,
    },
  ],
});
