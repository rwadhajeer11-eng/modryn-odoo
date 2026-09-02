from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynDiscountCode(models.Model):
    """A word the manager invents that takes a percentage off at the till.

    WHY A CODE AND NOT JUST TYPING THE NUMBER. The till already lets a
    saleswoman give a discount and demands a reason for it — that is the
    control on money leaving the shop. A code is the other half: the manager
    decides in advance that BRIDE10 is ten percent for the autumn fair, hands
    it out, and the woman at the counter types four letters instead of deciding
    an amount on her own. The reason writes itself, so the owner's screen reads
    "10% off — BRIDE10" rather than "10% off — she asked".

    PERCENT ONLY. A code that takes a fixed sum off is a code that gives a veil
    away for nothing and takes a tenth off a gown, and nobody notices until the
    month is counted.

    ARCHIVED, NEVER DELETED. Sales point at the reason a code wrote; a code
    removed from the table would leave those sentences unexplained.
    """

    _name = 'modryn.discount.code'
    _description = 'A discount code'
    _order = 'active desc, code'

    # Not translate=True, unlike the boutique's other lists: this is a word
    # somebody types at a counter, and the same word has to work whichever
    # language the screen happens to be in.
    code = fields.Char(required=True)
    percent = fields.Float(string="Percent off", required=True)
    note = fields.Char(help="What it is for, in your words. Nobody outside sees it.")
    active = fields.Boolean(default=True)
    times_used = fields.Integer(default=0, readonly=True)

    @api.constrains('percent')
    def _check_percent(self):
        for rule in self:
            # 100 is allowed - a staff gown, a photo shoot - but it is a
            # decision, not a typo, and anything past it is neither.
            if not (0 < rule.percent <= 100):
                raise ValidationError(
                    _("A discount is between 1 and 100 percent."))

    @api.constrains('code', 'active')
    def _check_code_unique(self):
        """One word, one meaning.

        Compared case-insensitively and against archived codes too: a retired
        BRIDE10 brought back at a different percentage would make last spring's
        sales unreadable.
        """
        for rule in self:
            if not rule.code:
                continue
            clash = self.with_context(active_test=False).search([
                ('id', '!=', rule.id), ('code', '=ilike', rule.code)], limit=1)
            if clash:
                raise ValidationError(_("That code already exists."))

    @api.model
    def modryn_find(self, code):
        """The live code somebody typed, whatever case they typed it in.

        Archived codes are NOT found: retiring one has to actually stop it
        working, or retiring it means nothing.
        """
        word = (code or '').strip()
        if not word:
            return self.browse()
        return self.search([('code', '=ilike', word)], limit=1)
