from odoo import SUPERUSER_ID, api

from odoo.addons.modryn_booking.schema_guard import seed


def migrate(cr, version):
    # post-migrate runs after the registry has loaded this module's models, so
    # the ORM is available; the install path is handed its env by Odoo instead.
    seed(api.Environment(cr, SUPERUSER_ID, {}))
