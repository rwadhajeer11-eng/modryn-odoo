from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        # The floor board is an OWL component living on a *frontend* page, and
        # /website/translations only ships web translations for the modules on
        # this list. Without us on it the board's terms are never sent to the
        # browser and it renders untranslated English on a Hebrew site — with no
        # error anywhere to say why.
        return super()._get_translation_frontend_modules_name() + ['modryn_staff']
