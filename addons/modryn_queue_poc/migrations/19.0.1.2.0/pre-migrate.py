from odoo.addons.modryn_queue_poc import schema_guard


def migrate(cr, version):
    schema_guard.dedupe(cr)
