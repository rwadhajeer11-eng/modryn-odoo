from odoo.addons.modryn_ops import schema_guard


def migrate(cr, version):
    schema_guard.dedupe(cr)
