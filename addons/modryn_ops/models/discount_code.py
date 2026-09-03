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

    # HOW LONG THE WORD LASTS. Three answers, because those are the three
    # things a manager actually means: hand it to one bride, hand it to the
    # first twenty, or run it until the fair closes on Saturday.
    limit_kind = fields.Selection(
        selection=[
            ('none', "As many times as they like"),
            ('times', "A set number of times"),
            ('until', "Until a date"),
        ],
        string="How long it lasts", default='none', required=True)
    max_uses = fields.Integer(string="How many times", default=1)
    use_until = fields.Date(string="Last day it works")

    @api.constrains('limit_kind', 'max_uses', 'use_until')
    def _check_limit(self):
        for rule in self:
            if rule.limit_kind == 'times' and rule.max_uses < 1:
                raise ValidationError(
                    _("A code has to work at least once."))
            if rule.limit_kind == 'until' and not rule.use_until:
                raise ValidationError(
                    _("Say which day is the last day."))

    def modryn_spent(self):
        """Why this code will not work today, in words — or nothing.

        A SENTENCE and not a boolean, because the till has to tell the woman at
        the counter which of the two it is. "BRIDE10 has been used already" and
        "BRIDE10 ran out on Saturday" send her to different places, and the
        first version of this returned True for both.
        """
        self.ensure_one()
        if self.limit_kind == 'times' and self.times_used >= self.max_uses:
            if self.max_uses == 1:
                return _("%s has already been used.", self.code)
            return _("%(code)s has been used its %(count)s times.",
                     code=self.code, count=self.max_uses)
        if self.limit_kind == 'until' and self.use_until                 and fields.Date.context_today(self) > self.use_until:
            return _("%(code)s stopped working on %(date)s.",
                     code=self.code,
                     date=self.use_until.strftime('%d.%m.%Y'))
        return False

    def modryn_left(self):
        """What the manager's screen prints in the "how long" column."""
        self.ensure_one()
        if self.limit_kind == 'times':
            return _("%(used)s of %(total)s used",
                     used=self.times_used, total=self.max_uses)
        if self.limit_kind == 'until' and self.use_until:
            return _("until %s", self.use_until.strftime('%d.%m.%Y'))
        return _("no limit")

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
