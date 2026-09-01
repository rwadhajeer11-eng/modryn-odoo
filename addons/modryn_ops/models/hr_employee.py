from odoo import fields, models

# Field -> human label for the audit diff, and the same shape res_partner.py
# uses: plain English strings, because this is what goes in the log row's
# `label` column and rewriting it later would rewrite history. The reader's own
# words come from FIELD_LABELS in audit_log.py, which is translated at read
# time.
#
# WHY THESE SIX. They are exactly what a member of staff can change about
# herself on /staff/profile — her name, the two numbers she can be reached on,
# where she lives, and how she is addressed. The owner has no other record of
# them: the profile form overwrites in place, so before this the answer to "she
# used to have a different number, what was it?" was gone the moment she saved.
#
# Roles and permission level are deliberately NOT here. They are not hers to
# change, they live on a different screen with a different audience, and mixing
# "she corrected a typo in her street" with "somebody made her a manager" in one
# list is how the second one stops being noticed.
STAFF_AUDITED_FIELDS = {
    'name': "Name",
    'work_phone': "Phone",
    'modryn_backup_phone': "Second phone",
    'modryn_city': "City",
    'modryn_street': "Street",
    'modryn_gender': "Gender",
}


class HrEmployee(models.Model):
    """Her own details, and a record of what they used to say.

    /staff/profile writes straight onto the employee, so every save overwrote
    the previous answer with nothing kept. That is fine for a street name and
    not fine for a phone number: the boutique rings the number on file, and when
    it turns out to be wrong the useful question is what it was before and who
    changed it.

    Logged in `write` rather than in the controller, and that is the point —
    the profile form is one of three ways these fields move (the owner's team
    form is another, and the Odoo back office a third), and an audit trail that
    only covers one route is one that quietly lies about the other two.
    """

    _inherit = 'hr.employee'

    def _modryn_staff_audit_repr(self, field_name):
        """What the field says, as a person would read it.

        A selection stores 'female' and means "Female"; printing the code would
        make the log a thing you need the source to interpret.
        """
        self.ensure_one()
        value = self[field_name]
        field = self._fields[field_name]
        if field.type == 'selection':
            # get_description, not field.selection: the list can be a callable,
            # and this is the same read the profile form validates against.
            options = dict(field.get_description(self.env)['selection'])
            return options.get(value, '') if value else ''
        if value is False or value is None:
            return ''
        return str(value)

    def write(self, vals):
        watched = [f for f in STAFF_AUDITED_FIELDS if f in vals]
        if not watched:
            return super().write(vals)
        # Read BEFORE the write, per record: `self` can be more than one
        # employee, and a single "before" would hang one woman's old number on
        # everybody in the set.
        before = {e.id: {f: e._modryn_staff_audit_repr(f) for f in watched}
                  for e in self}
        result = super().write(vals)
        for employee in self:
            for field_name in watched:
                new = employee._modryn_staff_audit_repr(field_name)
                # Only when it actually MOVED. A form posts every field on every
                # save, so logging writes rather than changes would file five
                # rows saying nothing each time she corrects one typo.
                if before[employee.id][field_name] != new:
                    self.env['modryn.audit.log'].modryn_log(
                        record=employee,
                        label=STAFF_AUDITED_FIELDS[field_name],
                        field=field_name,
                        old=before[employee.id][field_name],
                        new=new,
                    )
        return result
