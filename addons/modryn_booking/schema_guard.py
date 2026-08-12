"""Seeding that runs on the INSTALL path as well as on the upgrade path.

Odoo gives an upgrade a `migrations/<version>/post-migrate.py` and an install a
`post_init_hook`, and calls neither on both — see modryn_portal/schema_guard.py,
which is the annotated original. Both are wired here, and each covers a real
path:

  * `migrations/` covers `-u` on the databases that already exist. That set
    INCLUDES `modryn_template`, which carries this module installed at the old
    version like any other database.
  * `post_init_hook` covers a genuine `-i`, which is what `build_template.sh`
    does when the golden template is rebuilt from nothing.

`new_boutique.sh` runs NEITHER: it provisions with `createdb -T modryn_template`,
a Postgres-level clone, so a new boutique simply inherits whatever rows the
template already holds. That is why leaving `modryn_template` out of the upgrade
is not a cosmetic omission — every boutique cloned afterwards would open with an
empty grid, and a boutique with no rows here takes no bookings at all.

The ORM is used rather than raw SQL: this hook runs post-init, so the model and
its columns exist by now, and the migration builds an Environment for the same
reason.
"""

import logging

from .models.opening_hours import DEFAULT_HOURS

_logger = logging.getLogger(__name__)


def seed(env):
    """Fill the grid with the lattice that used to be hardcoded, once.

    Idempotent by emptiness, and `active_test=False` deliberately: an owner who
    archived every window has made a decision, and the next upgrade must not
    reopen the shop behind her by seeing "no rows" and helpfully seeding again.
    """
    hours = env['modryn.opening.hours'].sudo().with_context(active_test=False)
    if hours.search_count([]):
        return
    hours.create([
        {'weekday': weekday, 'start_hour': start_hour, 'end_hour': end_hour}
        for weekday, start_hour, end_hour in DEFAULT_HOURS
    ])
    _logger.info("modryn_booking: seeded %d opening-hours window(s)", len(DEFAULT_HOURS))


def post_init_hook(env):
    seed(env)
