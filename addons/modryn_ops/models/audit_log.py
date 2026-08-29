import pytz

from odoo import api, fields, models
from odoo.tools.translate import LazyTranslate

TZ = pytz.timezone('Asia/Jerusalem')


_lt = LazyTranslate(__name__)

# Every audited field's human name, in one place and lazily translated.
#
# _lt and not _: this dict is built at import time, where there is no request
# and no language, so _() would resolve once against nothing and freeze English
# in - the trap the Dresses nav label and the workshop's states both fell into.
#
# Keyed by the technical field name because that is what the row stores and what
# does not change with the reader. The two writers (calendar_event.py and
# res_partner.py) keep their own English maps: those are what goes in the
# `label` column as a fallback, and changing them would rewrite history.
FIELD_LABELS = {
    'modryn_outcome': _lt("Outcome"),
    'modryn_sale_amount': _lt("Sale amount"),
    'modryn_sale_items': _lt("Sale items"),
    'modryn_outcome_note': _lt("Outcome note"),
    'modryn_outcome_by_id': _lt("Closed by"),
    'modryn_employee_id': _lt("Stylist"),
}

class ModrynAuditLog(models.Model):
    """Who changed what, when — flat, append-only, owner-readable.

    Not mail.thread: staff and managers are portal users who cannot read
    chatter, and the owner page needs a flat table, not tracking values
    scattered across messages. Rows are only ever created through modryn_log,
    which records the REAL actor — sudo() elevates rights but does not change
    env.user (.memory/odoo-traps.md §7), so the identity survives every
    sudo()-shaped controller in this codebase.
    """

    _name = 'modryn.audit.log'
    _description = 'Audit trail'
    _order = 'create_date desc, id desc'

    user_id = fields.Many2one('res.users', ondelete='set null', index=True)
    # Denormalised: an audit row must keep saying who did it even after the
    # account is archived, renamed or deleted.
    actor_name = fields.Char()
    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True)
    # The human name of what changed, as it was at write time. Kept, but no
    # longer what the page shows: see FIELD_LABELS and _row() below. It remains
    # the fallback for a field nobody has named, and the record of what the
    # writer called it.
    label = fields.Char(required=True)
    field = fields.Char()
    old = fields.Char()
    new = fields.Char()

    @api.model
    def modryn_log(self, record, label, field=None, old=None, new=None):
        user = self.env.user
        self.sudo().create({
            'user_id': user.id if user and not user._is_public() else False,
            'actor_name': user.name if user else '',
            'model': record._name,
            'res_id': record.id,
            'label': label,
            'field': field or '',
            'old': old or '',
            'new': new or '',
        })

    def _row(self):
        self.ensure_one()
        # Translated HERE, from the language-free field name, so every reader
        # sees her own words and rows written months ago are fixed too. The
        # stored label is the fallback.
        local = pytz.utc.localize(self.create_date).astimezone(TZ)
        return {
            'id': self.id,
            'when': local.strftime('%d.%m.%Y %H:%M'),
            'actor': self.actor_name or '',
            'label': str(FIELD_LABELS[self.field]) if self.field in FIELD_LABELS
                     else self.label,
            'old': self.old or '',
            'new': self.new or '',
            'model': self.model,
            'res_id': self.res_id,
        }
