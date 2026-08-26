"""Backfill that runs on the INSTALL path as well as on the upgrade path.

Odoo gives an upgrade a `migrations/<version>/post-migrate.py` and an install a
`post_init_hook`, and calls neither on both — see modryn_booking/schema_guard.py,
which is the annotated original. Both are wired here, and each covers a real path:

  * `migrations/` covers `-u` on the databases that already exist, INCLUDING
    modryn_template — and `new_boutique.sh` provisions with `createdb -T`, a
    Postgres-level clone, so a boutique created afterwards inherits whatever the
    template holds. Leaving the template out is not cosmetic: every future
    boutique would open with its evening shifts labelled as mornings.
  * `post_init_hook` covers a genuine `-i`, which is what build_template.sh does
    when the golden template is rebuilt from nothing.

What it fixes: shift_type arrives with default='morning', so Odoo stamps every
template that predates the field as a morning — including the Thursday-late and
Saturday-evening ones this boutique exists to be able to describe.
"""

import logging

from .models.shift_template import type_for_hour

_logger = logging.getLogger(__name__)

# Run-once marker. The backfill GUESSES from the start hour, and a guess must
# never overwrite an owner's own answer — so it runs on the first upgrade and
# never again, rather than being "idempotent" by recomputing every time.
PARAM_DONE = 'modryn.roster.shift_types_backfilled'


def seed(env):
    Param = env['ir.config_parameter'].sudo()
    if Param.get_param(PARAM_DONE):
        return
    templates = env['modryn.shift.template'].sudo().with_context(
        active_test=False).search([])
    moved = 0
    for template in templates:
        guess = type_for_hour(template.start_hour)
        if guess != template.shift_type:
            template.shift_type = guess
            moved += 1
    Param.set_param(PARAM_DONE, '1')
    _logger.info("modryn_roster: classified %d of %d shift template(s) by start hour",
                 moved, len(templates))


def post_init_hook(env):
    seed(env)
