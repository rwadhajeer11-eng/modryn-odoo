from odoo import api, fields, models


class ResUsers(models.Model):
    """Two more things the platform owner has to know.

    The register is the one screen in this product that can see every boutique
    at once — who subscribes, on what, and who their partners are. A username
    and a password is what protects a shop floor; this asked for more.

    STORED AS HASHES, through the same passlib context Odoo hashes passwords
    with. That is not decoration: an identity number is somebody's real national
    ID, and a phone number identifies a person. Kept as plain columns they would
    sit readable in every backup and every database dump, in exchange for
    nothing — they are only ever COMPARED against what somebody typed, never
    read back or displayed. Nothing in this codebase can recover either value,
    including this module.

    Not on the login page's own model and not in ir.config_parameter, because
    they belong to a USER: a second platform account gets its own pair, and a
    config parameter would be one shared answer for everybody.
    """

    _inherit = 'res.users'

    # groups: only the platform owner may even see that these columns hold
    # something. Odoo's own password field is invisible in the same way.
    modryn_platform_phone = fields.Char(
        string="Platform phone (hashed)", copy=False,
        groups='modryn_platform.group_platform_owner')
    modryn_platform_idnum = fields.Char(
        string="Platform ID number (hashed)", copy=False,
        groups='modryn_platform.group_platform_owner')

    @api.model
    def _modryn_hash(self, value):
        """Hash one of the two extra answers, or False for an empty one."""
        value = (value or '').strip()
        if not value:
            return False
        return self._crypt_context().hash(value)

    def modryn_set_platform_factors(self, phone=None, idnum=None):
        """Set either extra answer. Never reads one back — there is no getter.

        Written as a method rather than left to a plain write() so the hashing
        cannot be forgotten by a caller: a phone stored in the clear here would
        look exactly like a phone stored correctly, and nothing would say so.
        """
        self.ensure_one()
        values = {}
        if phone is not None:
            values['modryn_platform_phone'] = self._modryn_hash(phone)
        if idnum is not None:
            values['modryn_platform_idnum'] = self._modryn_hash(idnum)
        if values:
            self.sudo().write(values)
        return True

    def modryn_platform_credentials_ok(self, login, phone, idnum, password):
        """All four, for an action that is not signing in.

        The delete button asks exactly what the door asks, because destroying a
        row somebody may be billing against deserves the same bar as walking in.

        EVERY part is checked and the results combined at the end, never
        short-circuited: a function that gives up on the first wrong answer
        finishes measurably sooner than one that checks all four, and that
        difference is readable from outside. It would say which parts were
        already right — the exact thing the silent refusal exists to hide.
        """
        self.ensure_one()
        # A wrong username is a wrong answer like any other. Compared here
        # rather than used to look somebody up, because the caller is already
        # signed in: the question is "are you who this session says", not
        # "who are you".
        login_ok = bool(login) and login.strip() == (self.login or '')

        password_ok = False
        if password:
            try:
                self.sudo()._check_credentials(
                    {'type': 'password', 'password': password},
                    {'interactive': False})
                password_ok = True
            except Exception:
                # Any refusal is a refusal. AccessDenied is the expected one;
                # anything else is still not a yes, and swallowing it here is
                # what keeps the caller from learning which happened.
                password_ok = False

        factors_ok = self.modryn_check_platform_factors(phone, idnum)
        return login_ok and password_ok and factors_ok

    def modryn_check_platform_factors(self, phone, idnum):
        """Do the typed phone and ID match what is on file?

        BOTH are always verified, and the two results are combined at the end
        rather than short-circuited on the first failure. A function that
        returns the moment the phone is wrong takes measurably less time than
        one that goes on to check the ID, and that difference is readable over a
        network — it would tell somebody which of the two they had already got
        right, which is the exact thing the single error message exists to hide.

        A user with nothing on file fails. The extra questions are not optional
        for whoever holds this account: an account that has never been given
        them would otherwise be an account they do not apply to.
        """
        self.ensure_one()
        context = self._crypt_context()
        stored = self.sudo().read(
            ['modryn_platform_phone', 'modryn_platform_idnum'])[0]

        def matches(typed, hashed):
            typed = (typed or '').strip()
            if not hashed or not typed:
                # Still spend the work: verifying against a dummy hash keeps a
                # user with nothing on file from answering faster than one who
                # has both, which would say which accounts are configured.
                context.dummy_verify()
                return False
            try:
                return context.verify(typed, hashed)
            except ValueError:
                return False

        phone_ok = matches(phone, stored.get('modryn_platform_phone'))
        idnum_ok = matches(idnum, stored.get('modryn_platform_idnum'))
        return phone_ok and idnum_ok
