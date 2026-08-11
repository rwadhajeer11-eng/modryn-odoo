{
    'name': 'MODRYN load-test capture (STAGING ONLY)',
    'version': '19.0.1.0.0',
    'summary': 'Reads OTP codes back for the k6 harness. Never install in production.',
    # Lives outside addons/ on purpose. Production's addons_path does not contain
    # loadtest/odoo_addons, so this module is not even discoverable there — the
    # directory is the first of three gates (path, enabled flag, secret).
    'depends': ['modryn_portal'],
    'data': ['security/ir.model.access.csv'],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
