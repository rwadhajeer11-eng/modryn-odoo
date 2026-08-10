from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynFittingRoom(models.Model):
    """A physical fitting room in this boutique.

    Owner-maintained data, like staff roles and garment pieces: a boutique with
    three rooms and a bridal suite must not need a developer to say so.
    """

    _name = 'modryn.fitting.room'
    _description = 'Fitting room'
    _order = 'sequence, name'

    # Plain Char, NOT translate=True: a translatable field is stored as jsonb and
    # uniqueness then compares whole JSON objects, so the same room typed in two
    # languages would pass as two rooms.
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(name)', "That fitting room already exists.")

    @api.constrains('name')
    def _check_name(self):
        for room in self:
            if room.name and not room.name.strip():
                raise ValidationError(_("A fitting room needs a name."))


class ModrynRoomOccupantMixin(models.AbstractModel):
    """A customer card that can be standing in a fitting room.

    The room matters for two things: the manager seeing at a glance which rooms
    are free, and an SOS call knowing WHERE to send help. A colleague paged to
    "Dana" has to cross the floor asking; paged to "Room 2" she walks straight
    there.
    """

    _name = 'modryn.room.occupant.mixin'
    _description = 'Customer card in a fitting room'

    modryn_room_id = fields.Many2one(
        'modryn.fitting.room',
        string="Fitting room",
        index=True,
        ondelete='set null',
    )

    def _modryn_is_open(self):
        """Is this card still live on the floor?

        A room is occupied only by an OPEN card. Nothing needs to free the room
        on finish — the same way occupancy is derived rather than toggled, so it
        cannot drift out of sync with reality.
        """
        raise NotImplementedError

    @api.constrains('modryn_room_id')
    def _check_room_single_occupant(self):
        """Two customers in one room is a real-world collision, not a data one.

        Enforced in Python rather than as a unique index because "occupied"
        depends on the card's STATE, and a partial unique index cannot span the
        two different models that share a room pool.
        """
        for record in self:
            room = record.modryn_room_id
            if not room or not record._modryn_is_open():
                continue
            for model in ('modryn.queue.entry', 'calendar.event'):
                Model = record.env[model].sudo()
                if 'modryn_room_id' not in Model._fields:
                    continue
                domain = [('modryn_room_id', '=', room.id)]
                if model == record._name:
                    domain.append(('id', '!=', record.id))
                for other in Model.search(domain):
                    if other._modryn_is_open():
                        raise ValidationError(_(
                            "%(room)s is already being used by %(who)s.",
                            room=room.name, who=other.display_name))
