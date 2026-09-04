from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ModrynDiscountCode(models.Model):
    """A word the manager invents that takes something off at the till.

    WHY A CODE AND NOT JUST TYPING THE NUMBER. The till already lets a
    saleswoman give a discount and demands a reason for it — that is the
    control on money leaving the shop. A code is the other half: the manager
    decides in advance that BRIDE10 is ten percent for the autumn fair, hands
    it out, and the woman at the counter picks four letters instead of deciding
    an amount on her own. The reason writes itself, so the owner's screen reads
    "10% off — BRIDE10" rather than "10% off — she asked".

    A PERCENTAGE OR A SUM, and the shop says which. A percentage is the safer
    answer and stays the default, for the reason this docstring used to refuse
    a sum outright: two hundred shekels is a tenth off a gown and the whole of
    a veil, and a code handed out without thinking about that gives one away.
    But it is the owner's decision to make, not this file's — so the screen
    makes her make it, and the sale clamps a sum at what the basket actually
    costs, so nothing ever goes below zero or hands money back.

    WHEN IT WORKS AND FOR HOW MANY ARE SEPARATE FACTS. The first version made
    them exclusive — a code was capped by a number of uses OR by a date — and
    that is not how a boutique thinks. "Twenty people, during the fair week" is
    one sentence and it needs both halves, so each is its own box and an empty
    box means no limit.

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

    # WHAT COMES OFF. Two columns rather than one number and a flag, because
    # they are different quantities: 10 means a tenth in one and ten shekels in
    # the other, and a single column would make every stored row ambiguous the
    # moment somebody switched a code's kind.
    value_kind = fields.Selection(
        selection=[('percent', "A percentage"), ('amount', "A sum of money")],
        string="What comes off", default='percent', required=True)
    percent = fields.Float(string="Percent off")
    amount = fields.Float(string="Shekels off")

    note = fields.Char(help="What it is for, in your words. Nobody outside sees it.")
    active = fields.Boolean(default=True)
    times_used = fields.Integer(default=0, readonly=True)

    # WHEN IT WORKS. Either end may be left open: a code with no first day
    # works from today, and one with no last day works until she stops it.
    starts_on = fields.Date(string="First day it works")
    use_until = fields.Date(string="Last day it works")
    # HOW MANY PEOPLE. Zero means as many as they like — an empty box reading
    # as "no limit" is what a person expects of an empty box, and a mode
    # dropdown to say the same thing was a decision the screen made her repeat.
    max_uses = fields.Integer(string="How many people may use it", default=0)

    @api.constrains('value_kind', 'percent', 'amount')
    def _check_value(self):
        for rule in self:
            if rule.value_kind == 'percent':
                # 100 is allowed — a staff gown, a photo shoot — but it is a
                # decision, not a typo, and anything past it is neither.
                if not 0 < rule.percent <= 100:
                    raise ValidationError(
                        _("A discount is between 1 and 100 percent."))
            elif rule.amount <= 0:
                raise ValidationError(
                    _("Say how many shekels come off."))

    @api.constrains('starts_on', 'use_until', 'max_uses')
    def _check_limit(self):
        for rule in self:
            if rule.max_uses < 0:
                raise ValidationError(
                    _("A code cannot be used a negative number of times."))
            if rule.starts_on and rule.use_until and rule.use_until < rule.starts_on:
                raise ValidationError(
                    _("The last day cannot come before the first."))

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

    def modryn_spent(self):
        """Why this code will not work today, in words — or nothing.

        A SENTENCE and not a boolean, because the till has to tell the woman at
        the counter which of them it is. "BRIDE10 has already been used",
        "BRIDE10 stopped working on Saturday" and "BRIDE10 does not start until
        Sunday" send her to three different places, and the first version of
        this returned True for all of them.

        Asked in the order a person would: has it started, has it finished, is
        it used up.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.starts_on and today < self.starts_on:
            return _("%(code)s does not start until %(date)s.",
                     code=self.code, date=self.starts_on.strftime('%d.%m.%Y'))
        if self.use_until and today > self.use_until:
            return _("%(code)s stopped working on %(date)s.",
                     code=self.code, date=self.use_until.strftime('%d.%m.%Y'))
        if self.max_uses and self.times_used >= self.max_uses:
            if self.max_uses == 1:
                return _("%s has already been used.", self.code)
            return _("%(code)s has been used its %(count)s times.",
                     code=self.code, count=self.max_uses)
        return False

    def modryn_status(self):
        """One word for the badge: waiting, finished, or nothing at all.

        Separate from modryn_spent(), which answers the till's question — "why
        will this not work right now" — in a whole sentence with the code's name
        in it. A badge in a narrow column needs a state, and the two are not the
        same question: a code that starts on Sunday will not work today and is
        not finished.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.starts_on and today < self.starts_on:
            return 'waiting'
        return 'finished' if self.modryn_spent() else ''

    def modryn_takes_off(self):
        """What this code takes off, as the shop would say it out loud."""
        self.ensure_one()
        if self.value_kind == 'amount':
            return '₪%s' % '{:,}'.format(int(round(self.amount)))
        return '%g%%' % self.percent

    def modryn_left(self):
        """What the manager's screen prints in the "how long it lasts" column.

        Both halves, joined, because both can be true at once — which is the
        whole point of putting them side by side. A code with neither says so
        rather than printing an empty cell, which reads as a missing answer.
        """
        self.ensure_one()
        parts = []
        if self.starts_on and self.use_until:
            parts.append(_(
                "%(first)s to %(last)s",
                first=self.starts_on.strftime('%d.%m.%Y'),
                last=self.use_until.strftime('%d.%m.%Y')))
        elif self.starts_on:
            parts.append(_("from %s", self.starts_on.strftime('%d.%m.%Y')))
        elif self.use_until:
            parts.append(_("until %s", self.use_until.strftime('%d.%m.%Y')))
        if self.max_uses:
            parts.append(_("%(used)s of %(total)s people",
                           used=self.times_used, total=self.max_uses))
        return ' · '.join(parts) if parts else _("no limit")

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
