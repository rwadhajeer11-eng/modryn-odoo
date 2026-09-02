import re

from odoo import fields, models

# The dial code a local number is assumed to belong to. Not read from
# country_id: Odoo stamps every fresh database with the United States and
# nothing in provisioning changes it, so trusting that field would turn
# 052-555-0001 into an American number. This product sells in shekels, in
# Hebrew, in Israel; when it sells somewhere else this becomes a setting and
# not a guess.
DEFAULT_DIAL_CODE = '972'


class ResCompany(models.Model):
    """The boutique's own contact details, as a customer needs them.

    res.company already carries the phone, the email and the address, and the
    header and the footer already read them. What it has no concept of is
    WhatsApp, which for a bridal shop is not a nice-to-have: a bride asks about
    a dress at eleven at night and she asks on WhatsApp.
    """

    _inherit = 'res.company'

    modryn_whatsapp = fields.Char(
        string="WhatsApp number",
        help="Leave this empty to use the shop's phone number. Fill it in only "
             "when WhatsApp is on a different line.")

    def modryn_whatsapp_number(self):
        """The number WhatsApp wants: digits only, with the country in front.

        WHY THIS IS NOT JUST THE PHONE FIELD. wa.me refuses anything but digits
        in international form - no plus, no dashes, no leading zero - and a
        boutique writes her number the way she says it out loud, "052-555-0001".
        Converting it here means she never has to know that.

        A number she typed in international form already (+972…, 00972…, or
        bare 972…) is left as it is: she knew what she was doing.
        """
        self.ensure_one()
        raw = (self.modryn_whatsapp or self.phone or '').strip()
        if not raw:
            return ''

        international = raw.startswith('+') or raw.lstrip('+ ').startswith('00')
        digits = re.sub(r'\D', '', raw)
        if not digits:
            return ''
        if international:
            return digits[2:] if digits.startswith('00') else digits
        if digits.startswith('0'):
            return DEFAULT_DIAL_CODE + digits[1:]
        if digits.startswith(DEFAULT_DIAL_CODE):
            return digits
        return DEFAULT_DIAL_CODE + digits

    def modryn_whatsapp_url(self):
        """The link to open a chat, or nothing at all.

        Empty rather than a broken wa.me/ URL: a button that opens WhatsApp on
        no number is worse than no button, and every template that draws this
        guards on it.
        """
        number = self.modryn_whatsapp_number()
        return ('https://wa.me/%s' % number) if number else ''
