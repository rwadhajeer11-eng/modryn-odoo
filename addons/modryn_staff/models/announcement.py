from odoo import api, fields, models


class ModrynAnnouncement(models.Model):
    """Something the manager wants the team to know, said once, to everybody.

    Every other message in this product is raised BY an event - a task
    assigned, a rota published, a customer waiting. There was no way to say
    "the delivery is late, nobody promise Thursday" except walking the floor
    and repeating it, which is how half a team ends up not knowing.

    It carries its own recipients rather than fanning out and forgetting: an
    announcement sent by mistake has to be un-sendable, and unsending means
    taking it off the bells it already rang. A fan-out with no record of what
    it produced can only be apologised for.

    The AUTHOR's name is denormalised, the same decision audit.log and the
    notification model both make: the row has to keep reading correctly after
    the person who wrote it leaves and her account is archived.
    """

    _name = 'modryn.announcement'
    _description = 'A message from the manager to the team'
    _order = 'create_date desc, id desc'

    body = fields.Text(required=True)
    # Empty means EVERYONE. Not a flag plus a list, which is two ways of saying
    # the same thing and eventually says two different ones - the same shape
    # modryn.sos.call uses for "any manager".
    employee_ids = fields.Many2many(
        'hr.employee', string="Who it went to",
        help="Empty means the whole team.")
    author_id = fields.Many2one('hr.employee', ondelete='set null')
    author_name = fields.Char(string="Who sent it")

    def _recipients(self):
        """Who this actually reaches.

        Never the author: she is the one who wrote it, and a bell for her own
        words is noise - the same rule modryn_notify already applies.
        """
        self.ensure_one()
        if self.employee_ids:
            people = self.employee_ids
        else:
            people = self.env['hr.employee'].sudo().search([
                ('modryn_level', 'in', ('owner', 'manager', 'staff')),
            ])
        return people - self.author_id

    @api.model
    def modryn_publish(self, body, employees, author):
        """Say it, and ring the bells.

        Returns the announcement. The bells are raised here rather than by the
        caller so that publishing and being heard cannot come apart - a route
        that remembered one and forgot the other would look like it worked.
        """
        body = (body or '').strip()
        if not body:
            return self.browse()
        # Worked out BEFORE the row exists. The author is never a recipient of
        # her own words, so "only me" resolves to nobody - and an announcement
        # that rang no bell must not sit in the sent list looking delivered.
        if employees and not (employees - author if author else employees):
            return self.browse()
        record = self.sudo().create({
            'body': body,
            'employee_ids': [(6, 0, employees.ids)] if employees else [(5,)],
            'author_id': author.id if author else False,
            'author_name': author.name if author else False,
        })
        Notification = self.env['modryn.staff.notification']
        for person in record._recipients():
            Notification.modryn_notify(person, body, actor=author, record=record)
        return record

    def modryn_unsend(self):
        """Sent by mistake. Take it off every bell it rang, then drop it.

        The notifications are found by the model/res_id pair the notification
        model already carries for exactly this - a message deleted from the
        manager's list while still sitting in eight people's bells has not been
        unsent, it has only been hidden from the person who regretted it.
        """
        Notification = self.env['modryn.staff.notification'].sudo()
        for record in self:
            Notification.search([
                ('res_model', '=', self._name),
                ('res_id', '=', record.id),
            ]).unlink()
        return self.sudo().unlink()
