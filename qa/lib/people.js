// Who to sign in as.
//
// Logins are per-tenant, because seed_staff.py seeds people, not roles, and
// res.users.login is unique per DATABASE — one database per boutique is the
// tenancy model. bella and noga were seeded with different names, so a spec
// that hardcoded `sara` was a spec that only ran on bella.
//
// That mattered the moment the suite had to move: bella holds live Twilio
// credentials, so every @writes act has to run on noga, and every hardcoded
// bella login became a 303 that read as a broken route.
const PEOPLE = {
  bella: { owner: 'miri', manager: 'sara', staff: 'rotem' },
  noga: { owner: 'tamar', manager: 'yael', staff: 'dana' },
};

// Overridable, because a production QA tenant is seeded by whoever provisions
// it and this file cannot know those names.
exports.peopleFor = (tenant) => ({
  owner: process.env.QA_OWNER || (PEOPLE[tenant] || {}).owner,
  manager: process.env.QA_MANAGER || (PEOPLE[tenant] || {}).manager,
  staff: process.env.QA_STAFF || (PEOPLE[tenant] || {}).staff,
});

exports.requirePeople = (tenant) => {
  const p = exports.peopleFor(tenant);
  const missing = Object.entries(p).filter(([, v]) => !v).map(([k]) => k);
  if (missing.length) {
    throw new Error(
      `no seeded logins known for tenant "${tenant}" (missing: ${missing.join(', ')}). ` +
        `Set QA_MANAGER / QA_STAFF / QA_OWNER, or add the tenant to qa/lib/people.js.`
    );
  }
  return p;
};
