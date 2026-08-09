from odoo import api, fields, models

# One channel per database is enough: a tenant IS a database here, so there is
# no cross-boutique leak to design around — the websocket is already scoped.
QUEUE_CHANNEL = 'modryn_queue'


class ModrynQueueEntry(models.Model):
    _name = 'modryn.queue.entry'
    _description = 'Walk-in queue entry'
    # Fair order is the whole point of a queue: first to submit is first served.
    _order = 'create_date asc, id asc'

    name = fields.Char(required=True)
    phone = fields.Char()
    client_type = fields.Selection(
        selection=[('bride', 'כלה'), ('evening', 'ערב')],
        default='bride',
        required=True,
    )
    state = fields.Selection(
        selection=[('waiting', 'ממתינה'), ('called', 'נקראה'), ('done', 'הסתיימה')],
        default='waiting',
        required=True,
        index=True,
    )

    def _payload(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or '',
            'client_type': self.client_type,
            'state': self.state,
        }

    def _notify_board(self):
        """Push to every open board. Called after the row is committed-visible."""
        for entry in self:
            self.env['bus.bus']._sendone(QUEUE_CHANNEL, 'modryn_queue/update', entry._payload())

    @api.model_create_multi
    def create(self, vals_list):
        entries = super().create(vals_list)
        entries._notify_board()
        return entries

    def write(self, vals):
        res = super().write(vals)
        # Only state changes matter to the board; anything else is noise.
        if 'state' in vals:
            self._notify_board()
        return res

    def action_call_next(self):
        self.write({'state': 'called'})

    def action_done(self):
        self.write({'state': 'done'})
