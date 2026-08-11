"""Clear the rows that would make the new unique indexes un-buildable.

PRE phase because _auto_init applies the indexes after this runs; cleaning up
afterwards would be cleaning up after a failure that already happened.

The routine itself lives in schema_guard so that the install path — which gets a
pre_init_hook instead of a migration, and which is the path every cloned tenant
takes — runs byte-identical SQL rather than a second, drifting copy.
"""

from odoo.addons.modryn_portal.schema_guard import dedupe


def migrate(cr, version):
    dedupe(cr)
