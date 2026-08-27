from odoo import api, fields, models


class ModrynStaffNotification(models.Model):
    """Something that happened which a particular woman needs to know about.

    Shaped on modryn.audit.log rather than on Odoo's mail.message: the
    recipients here are PORTAL users, and mail.message drags a whole
    messaging stack that portal accounts cannot reach anyway.

    NOT the bus. bus.bus is a doorbell - its rows are garbage-collected after a
    day and only reach sockets that happen to be open at push time, so a
    saleswoman who is off shift when the manager publishes the rota would never
    learn it happened. Unread state has to survive being away, so it lives in
    its own table and the bus can be added later as a nudge on top.

    actor_name is DENORMALISED, the same decision audit.log makes: the row has
    to keep reading correctly after the person who caused it leaves and her
    account is archived.
    """

    _name = 'modryn.staff.notification'
    _description = 'Something a staff member should see'
    _order = 'create_date desc, id desc'

    employee_id = fields.Many2one(
        'hr.employee', required=True, index=True, ondelete='cascade',
        string="Who it is for")
    body = fields.Char(required=True)
    # Where it came from, so a future version can link to the thing itself.
    # Deliberately a loose model/res_id pair rather than a reference field: the
    # targets live in four different modules and half of them are optional
    # installs.
    res_model = fields.Char()
    res_id = fields.Integer()
    actor_name = fields.Char(string="Who did it")
    read_at = fields.Datetime(index=True)

    @api.model
    def modryn_notify(self, employee, body, actor=None, record=None):
        """Raise one. Never raises for a woman acting on herself.

        Created through sudo() by callers that are already group-checked - this
        model has NO ACL row for the boutique groups on purpose, matching
        modryn.sos.call: portal staff reach it only through a controller that
        checks membership itself.
        """
        if not employee or not body:
            return self.browse()
        if actor and actor.id == employee.id:
            # She just did it herself; telling her is noise.
            return self.browse()
        values = {
            'employee_id': employee.id,
            'body': body,
            'actor_name': actor.name if actor else False,
        }
        if record is not None and record:
            values['res_model'] = record._name
            values['res_id'] = record.id
        return self.sudo().create(values)

    @api.model
    def modryn_unread_count(self, employee):
        if not employee:
            return 0
        return self.sudo().search_count([
            ('employee_id', '=', employee.id), ('read_at', '=', False)])

    def modryn_mark_read(self):
        self.sudo().filtered(lambda n: not n.read_at).write(
            {'read_at': fields.Datetime.now()})
        return True
