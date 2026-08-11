import pytz

from odoo import api, fields, models

TZ = pytz.timezone('Asia/Jerusalem')


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
    # The human name of what changed ("Outcome", "Stylist") — old and new carry
    # the values. Rendered as "Outcome: — → sold" on /manage/audit.
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
        local = pytz.utc.localize(self.create_date).astimezone(TZ)
        return {
            'id': self.id,
            'when': local.strftime('%d.%m.%Y %H:%M'),
            'actor': self.actor_name or '',
            'label': self.label,
            'old': self.old or '',
            'new': self.new or '',
            'model': self.model,
            'res_id': self.res_id,
        }
