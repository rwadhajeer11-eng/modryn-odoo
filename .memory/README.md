# Memory — what this project learned

Durable knowledge for `modryn-odoo`. Committed on purpose: these files existed as untracked
scratch once and vanished between two commands on the same day. Anything worth knowing twice
belongs in git.

## Read in this order

| File | Read it when |
|---|---|
| [`odoo-traps.md`](odoo-traps.md) | **Before writing any addon code.** Twelve failures that produce no error and no log line. This is the file that saves days. |
| [`decisions.md`](decisions.md) | Before proposing a design. Every entry was argued through and settled — arriving at the opposite conclusion means re-running an argument that already happened. |
| [`bugs-and-fixes.md`](bugs-and-fixes.md) | When something behaves oddly. Real bugs that shipped here, with root causes. Several were invisible until specifically hunted. |
| [`verification-lessons.md`](verification-lessons.md) | Before trusting a test result. The harness produced more false readings on this project than the code produced real bugs. |

Project state and what to do next live in [`../.planning/`](../.planning/). The design tokens
are in [`../docs/design-system.md`](../docs/design-system.md).

## The one rule

**These notes orient; the code decides.** Every claim here was true when written and carries a
file path so it can be re-checked. Before acting on a specific signature, default, colour or
control-flow claim, open the file it names. A note that has drifted is worse than no note, so
correct one the moment you catch it rather than working around it.

## Where the other knowledge lives

- `docs/scorecard.md` — the evaluation verdict and its evidence. The actual deliverable.
- `docs/walkthrough.md` — thirteen replayable acts covering every feature end to end.
- `docs/context-prompt.md` — paste into a fresh session to bootstrap it.
- `README.md` — setup, tenancy, environment.
- `scripts/verify.sh` — 85 checks. Run it before believing anything works.
