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
    # A member of staff's own details, changed by her on /staff/profile or by
    # the owner on the team screen. Same treatment as the rest: named here so
    # the reader sees her own language, whatever the row was written in.
    'name': _lt("Name"),
    'work_phone': _lt("Phone"),
    'modryn_backup_phone': _lt("Second phone"),
    'modryn_city': _lt("City"),
    'modryn_street': _lt("Street"),
    'modryn_gender': _lt("Gender"),
    # A sale's own fields. Without these the audit fell back to the label
    # STORED at the time, which is the English the writer's code was written
    # in - "Discount" sat in a Hebrew table and in an Arabic one.
    # discount_AMOUNT is the one the audit records - the money taken off,
    # computed and stored - and naming discount_value alone left the row
    # printing the English label it was written with. Both are here because
    # both can be logged.
    'discount_amount': _lt("Discount"),
    'discount_value': _lt("Discount"),
    'discount_reason': _lt("Why the discount"),
    'discount_kind': _lt("Kind of discount"),
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

    def _row_list(self):
        """Every row in this set, formatted - the recordset form of _row().

        Added for the team cards, which ask for one person's last few edits and
        would otherwise loop and call _row() per record in a controller. One
        place formats an audit row; there is no second copy to drift.
        """
        return [record._row() for record in self]

    def _actor(self):
        """Who did it, and "the system" when nobody did.

        Cron jobs, seeders and maintenance scripts run as Odoo's root account,
        so the trail read "OdooBot" ninety times down the owner's page. Matched
        on the USER rather than on the stored text: actor_name is denormalised
        deliberately, so that a row keeps naming whoever did it even after the
        account is renamed - and a name match would break on exactly that.
        """
        root = self.env.ref('base.user_root', raise_if_not_found=False)
        if root and self.user_id and self.user_id.id == root.id:
            return str(_lt("The system"))
        return self.actor_name or ''

    @staticmethod
    def _money(text):
        """"6700.0" as 6,700 - and anything unparseable exactly as stored.

        The stored text is str() of a float. Nothing is rounded away: a price
        with agorot on it keeps them, because a boutique that wrote 6,700.50
        meant the fifty.
        """
        try:
            amount = float(text)
        except (TypeError, ValueError):
            return text
        if amount == int(amount):
            return '{:,}'.format(int(amount))
        return '{:,.2f}'.format(amount)

    def _value(self, text):
        """A stored selection label, in the reader's language.

        The column holds the ENGLISH label - that is what _modryn_audit_repr
        wrote, in every row, since the first one - so the map is keyed on
        English and old rows come out translated with no migration.

        Anything that is not a selection (a price, a note, a name) is returned
        untouched: those are the boutique's own words and not ours to change.
        """
        if not text or not self.field or self.model not in self.env:
            return text
        field = self.env[self.model]._fields.get(self.field)
        if field is not None and field.type in ('float', 'monetary'):
            return self._money(text)
        # selection can be a callable or the name of a method; only a plain
        # list can be read backwards from its labels.
        if not field or field.type != 'selection'                 or not isinstance(field.selection, list):
            return text
        translated = dict(self.env[self.model].sudo().fields_get(
            [self.field])[self.field]['selection'])
        by_english = {label: translated.get(code, label)
                      for code, label in field.selection}
        return by_english.get(text, text)

    def _row(self):
        self.ensure_one()
        # Translated HERE, from the language-free field name, so every reader
        # sees her own words and rows written months ago are fixed too. The
        # stored label is the fallback.
        local = pytz.utc.localize(self.create_date).astimezone(TZ)
        return {
            'id': self.id,
            'when': local.strftime('%d.%m.%Y %H:%M'),
            'actor': self._actor(),
            'label': str(FIELD_LABELS[self.field]) if self.field in FIELD_LABELS
                     else self.label,
            'old': self._value(self.old or ''),
            'new': self._value(self.new or ''),
            'model': self.model,
            'res_id': self.res_id,
        }
