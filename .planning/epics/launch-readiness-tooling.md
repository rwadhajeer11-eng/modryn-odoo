# Epic: the tooling the launch gates actually need

_Opened 2026-08-12 on `main`. Baseline: `scripts/verify.sh` 326 passed / 0 failed / 2 skipped._

## The question this epic answers

`deploy/` is finished. Twenty-one files carry a bare-metal Ubuntu box from empty to serving, and
`deploy/README.md` §11 lists ten gates that stand between that box and a real bride booking a real
fitting. This epic is not about deployment. It is about the six gates whose **evidence does not
exist yet**, and the three whose evidence is currently a check that cannot fail.

Nothing here is a feature. Every line of it is a way of finding out whether something is true.

## Why now, and not after the box exists

Three of the five features must land **before** the first commit is deployed, because
`deploy/scripts/deploy.sh` runs `scripts/verify.sh` as its own gate and rolls back on a non-zero
exit. A suite that cannot address production hostnames gates nothing. And one of them — E4 — must
land before the load campaign, which must itself run before the first real boutique exists.

## The three gates that pass for the wrong reason

Found while reading the runbook against the source. Each would have been believed.

| # | Where | What is wrong |
|---|---|---|
| 1 | `deploy/README.md` §8 check 1 | `journalctl -u odoo \| grep nope` expects no output. `deploy/odoo.conf.prod:208` sets `log_level = warn` and the only handler override is `res_users:INFO`, so production Odoo logs **no request lines at all**. The output is empty whether the request reached Python or not — including in the exact failure the check exists to catch (unknown subdomain → 303 → `/web/database/selector`, which raises nothing). |
| 2 | `deploy/README.md:507`, gate 10 | `grep ' 429 ' /var/log/nginx/modryn.log`. `log_format modryn_load` (`deploy/nginx/modryn-http.conf:36`) writes `status=$status`, so the line reads `status=429` and never space-429-space. The gate passes on grep syntax, on every box, forever. |
| 3 | gate 10, again | Even with the grep fixed it is unsatisfiable. `modryn-http.conf:61-73` exempts `127.0.0.1/32` from `limit_req` unconditionally and §7 tells you to add `LOADGEN_IP` to that exemption — an exempt generator **cannot** emit a 429. Zero is guaranteed on a box with no rate limiting at all. |

Gate 10 is restated in E2 and E4 as two numbers with a control: (a) zero `status=429` from clients
who are *not* the exempted generator during the ramp window, AND (b) `verify_edge.sh` E4 proving
429 is reachable at all in the same session. One without the other is the "healthy jail with zero
bans" mistake wearing a different hat.

## The five features

Ordered by what unblocks what, not by size.

| # | Feature | Gates it closes | Spec |
|---|---|---|---|
| E1 | `BASE_HOST` for `scripts/verify.sh` | 1 | [`launch-e1-base-host.md`](../specs/launch-e1-base-host.md) |
| E2 | `deploy/scripts/verify_edge.sh` — the nginx layer | 2, 3, 3b, 3c, 4, and half of 10 | [`launch-e2-verify-edge.md`](../specs/launch-e2-verify-edge.md) |
| E3 | Self-hosted type | 6 | [`launch-e3-self-hosted-fonts.md`](../specs/launch-e3-self-hosted-fonts.md) |
| E4 | A load harness that can measure through nginx | 9, and half of 10 | [`launch-e4-k6-through-nginx.md`](../specs/launch-e4-k6-through-nginx.md) |
| E5 | Browser QA — the assertions curl cannot make | none, and that is the point | [`launch-e5-browser-qa.md`](../specs/launch-e5-browser-qa.md) |

E5 closes no gate. It is the only permanent asset in the list: every other feature is evidence
gathered once for a launch, and E5 is the thing that notices when a bundle stops compiling six
months from now. It is last because no gate blocks on it, not because it matters least.

## What this epic deliberately does not do

- **It does not deploy anything.** `deploy/` already works; running it is an operator sequence, not
  a build.
- **It does not add CI.** There is none today. Adding it is a real decision about where tests run
  and who watches them, and smuggling it in under "launch tooling" is how that decision gets made
  by nobody.
- **It does not touch SMTP, payments or horizontal scaling.** All three are absent by design and
  documented as Phase-2 in `docs/scorecard.md`. Quietly filling a documented gap is worse than
  leaving it: it retires the worry that would otherwise have caught it.
- **It does not fix the two `skip()`s in `verify.sh`.** Both are fixture age — bella holds no
  future booking — not gaps in the code. They fire again the moment anyone books ahead.

## Done means

1. `./scripts/verify.sh` on dev: **≥326 passed, 0 failed**, and the count has not silently dropped.
2. `BASE_HOST=nonexistent.invalid ./scripts/verify.sh` exits **1**. Misconfiguration is loud.
3. `deploy/scripts/verify_edge.sh --remote-only` runs, and its four on-box checks `skip()` **with
   the reason printed** — a green laptop run can never read as a full pass.
4. The four-path font probe reports `html_3p=0 css_3p=0 faces>=2 theme_marker>0` on every row.
5. `node loadtest/k6/lib/session.check.mjs` rejects a boutique-shaped target and accepts a load one.
6. `cd qa && npm test` passes seven specs against the dev server.

Each of those is a command with an exit code. None of them is a file that says something is true.
