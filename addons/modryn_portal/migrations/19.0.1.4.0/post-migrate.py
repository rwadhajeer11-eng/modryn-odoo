"""Prove the widened index is actually there before calling the upgrade done.

Odoo does not: registry.finalize_constraints() warns and moves on, "this is not a
deployment showstopper". For the index that decides how many brides fit in one
hour it is exactly that.

Existence is no longer the whole question — assert_indexes also checks this one
index keys on modryn_slot_seat, because a surviving (start) index has the right
name and the wrong meaning: the owner sets capacity to 2, the form saves, and the
second customer is told the time was just taken.

One ordering caveat, stated because it is not visible from here: on the `-u` path
a constraint that failed during _auto_init is retried in finalize_constraints(),
which runs AFTER every migration phase including 'end' (loading.py:501, :510). So
this can in principle fail on an index that the retry would have created. That
retry exists for cross-module ordering, and both columns these indexes touch are
owned by this module and its declared dependency — so the realistic reading of a
failure here is a genuinely missing index, and a re-run is cheap either way.
"""

from odoo.addons.modryn_portal.schema_guard import assert_indexes


def migrate(cr, version):
    assert_indexes(cr)
