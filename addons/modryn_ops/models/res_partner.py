from odoo import fields, models

# Field -> human label for the audit diff. A bride's budget and measurements
# are sensitive data; who edited them is exactly what the owner will one day
# need to know.
CRM_AUDITED_FIELDS = {
    'modryn_wedding_date': "Wedding date",
    'modryn_budget': "Budget",
    'modryn_party_notes': "Party",
    'modryn_measurements': "Measurements",
    'modryn_notes': "Notes",
    'modryn_category': "Category",
}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    modryn_wedding_date = fields.Date()
    # Role-gated: the profile controller includes this key in its payload only
    # for managers. Under this codebase's security model — portal users, no ORM
    # access, controller-built dicts — omitting the key IS the field-level ACL.
    modryn_budget = fields.Float()
    modryn_party_notes = fields.Char()
    # Free text, not a measurement grid: every seamstress writes her numbers
    # her own way, and a rigid form is how they end up on paper instead.
    modryn_measurements = fields.Text()
    modryn_notes = fields.Text()
    # Written only by the outcome flow — sold marks her purchased, not-sold
    # marks her not-purchased unless she already bought. Never set by hand.
    modryn_category = fields.Selection([
        ('purchased', "Purchased"),
        ('not_purchased', "Not purchased"),
    ])

    def _modryn_audit_repr(self, field_name):
        self.ensure_one()
        value = self[field_name]
        field = self._fields[field_name]
        if field.type == 'selection':
            return dict(field.selection).get(value, '') if value else ''
        if value is False or value is None:
            return ''
        return str(value)

    def write(self, vals):
        watched = [f for f in CRM_AUDITED_FIELDS if f in vals]
        if not watched:
            return super().write(vals)
        before = {p.id: {f: p._modryn_audit_repr(f) for f in watched} for p in self}
        result = super().write(vals)
        for partner in self:
            for field_name in watched:
                new = partner._modryn_audit_repr(field_name)
                if before[partner.id][field_name] != new:
                    self.env['modryn.audit.log'].modryn_log(
                        record=partner,
                        label=CRM_AUDITED_FIELDS[field_name],
                        field=field_name,
                        old=before[partner.id][field_name],
                        new=new,
                    )
        return result
