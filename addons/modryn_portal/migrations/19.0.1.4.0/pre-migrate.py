"""Widen the slot index from one booking per hour to `capacity` of them.

PRE phase because _auto_init applies the indexes after this runs. Three things
have to be true before it does, and all three live in schema_guard so the
install path — which gets a pre_init_hook instead of a migration, and which is
the path every cloned tenant takes — runs byte-identical SQL rather than a
second, drifting copy:

  * calendar_event.modryn_slot_seat exists and holds 0 everywhere, because the
    new index keys on it and the field is required. It is modryn_booking's
    field, so a `-u modryn_portal` alone would never create it.
  * the pre-capacity index is gone if it is still keyed on (start) alone. Odoo
    normally replaces it itself, but keeps any index carrying no COMMENT — and a
    kept one pins every window to a single fitting while reporting success.
  * no two live bookings share a (start, seat) pair.
"""

from odoo.addons.modryn_portal.schema_guard import dedupe


def migrate(cr, version):
    dedupe(cr)
