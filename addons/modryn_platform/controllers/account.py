import odoo
from odoo import _, http
from odoo.http import request

GROUP_PLATFORM = 'modryn_platform.group_platform_owner'

# Short enough to type at a counter, long enough not to be guessed. Odoo has no
# password policy of its own in Community, so the one rule this product does
# want is stated here rather than assumed.
MIN_PASSWORD = 10


class ModrynPlatformAccount(http.Controller):
    """The platform owner's own record, and the four answers she signs in with.

    Everything here used to be a shell script. That was defensible while the
    only person who could run it was the person who built the machine; it is
    not defensible as the way somebody sets up their own company.

    THREE FORMS, not one. The company's address, the sign-in answers and the
    password are three different decisions carrying three different risks:
    correcting a phone number should not mean retyping a password, and a
    refused password should not throw away a street typed a minute earlier.
    """

    def _owner(self):
        user = request.env.user
        if user._is_public() or not user.has_group(GROUP_PLATFORM):
            return None
        return user

    def _context(self, error=None, saved=False):
        user = request.env.user
        company = user.company_id.sudo()
        # read() rather than reaching for the fields directly: the two factor
        # columns are group-restricted, and read() answers "is there something
        # there" without this screen ever holding the values themselves — it
        # could not show them anyway, they are hashes.
        stored = user.sudo().read(
            ['modryn_platform_phone', 'modryn_platform_idnum'])[0]
        return {
            'here': 'account',
            'title': _("My company"),
            'error': error,
            'saved': saved,
            'company': {
                'name': company.name or '',
                'email': company.email or '',
                'phone': company.phone or '',
                'vat': company.vat or '',
                'street': company.street or '',
                'city': company.city or '',
            },
            'account': {
                'login': user.login or '',
                'has_phone': bool(stored.get('modryn_platform_phone')),
                'has_id': bool(stored.get('modryn_platform_idnum')),
                # Built here, because a string quoted inside a t-attf
                # expression never reaches a .po - the exporter does not read
                # inside one, so it would sit on the screen in English with no
                # missing translation to report. The aria-labels on the
                # supervisor's screen were the same mistake.
                'phone_hint': (_("Set — type to replace")
                               if stored.get('modryn_platform_phone')
                               else _("Not set yet")),
                'id_hint': (_("Set — type to replace")
                            if stored.get('modryn_platform_idnum')
                            else _("Not set yet")),
            },
        }

    def _render(self, error=None, saved=False):
        return request.render('modryn_platform.platform_account',
                              self._context(error=error, saved=saved))

    @http.route('/platform/account', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def account(self, saved=None, **kw):
        if not self._owner():
            return request.not_found()
        return self._render(saved=bool(saved))

    @http.route('/platform/account/company', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def account_company(self, **post):
        user = self._owner()
        if not user:
            return request.not_found()
        name = (post.get('company_name') or '').strip()
        if not name:
            return self._render(error=_("The company needs a name."))
        user.company_id.sudo().write({
            'name': name,
            'email': (post.get('company_email') or '').strip() or False,
            'phone': (post.get('company_phone') or '').strip() or False,
            'vat': (post.get('company_vat') or '').strip() or False,
            'street': (post.get('company_street') or '').strip() or False,
            'city': (post.get('company_city') or '').strip() or False,
        })
        return request.redirect('/platform/account?saved=1')

    def _password_holds(self, user, password):
        """Is this her current password?

        Verified by authenticating, which is the only check that goes through
        Odoo's own hashing and its own rate limiting. A comparison written here
        would be a second implementation of the one thing worth not writing
        twice.
        """
        if not password:
            return False
        try:
            user.sudo()._check_credentials(
                {'type': 'password', 'password': password},
                {'interactive': False})
        except odoo.exceptions.AccessDenied:
            return False
        return True

    @http.route('/platform/account/login', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def account_login(self, **post):
        user = self._owner()
        if not user:
            return request.not_found()

        # Her CURRENT password, to change any of these. Without it, anybody who
        # reached a session she left open could rewrite the very answers that
        # keep them out — which would make the extra two factors weaker than
        # having none, because they would look like protection.
        if not self._password_holds(user, post.get('current_password')):
            return self._render(error=_("That password is not right."))

        login = (post.get('username') or '').strip()
        if not login:
            return self._render(error=_("You need a username."))

        phone = (post.get('factor_phone') or '').strip()
        idnum = (post.get('factor_id') or '').strip()

        # BLANK MEANS LEAVE IT, not clear it. The two are stored as hashes and
        # cannot be shown back, so the form has nothing to put in the box — an
        # empty field is somebody not changing it, and treating that as "erase"
        # would quietly lock the account out on the next save of an unrelated
        # field.
        if login != user.login:
            existing = request.env['res.users'].sudo().search(
                [('login', '=', login), ('id', '!=', user.id)], limit=1)
            if existing:
                return self._render(error=_("That username is taken."))
            user.sudo().login = login
        user.modryn_set_platform_factors(
            phone=phone or None, idnum=idnum or None)
        return request.redirect('/platform/account?saved=1')

    @http.route('/platform/account/password', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def account_password(self, **post):
        user = self._owner()
        if not user:
            return request.not_found()

        if not self._password_holds(user, post.get('old_password')):
            return self._render(error=_("That password is not right."))

        new = post.get('new_password') or ''
        again = post.get('new_password2') or ''
        if new != again:
            return self._render(error=_("The two new passwords are not the same."))
        if len(new) < MIN_PASSWORD:
            return self._render(error=_(
                "A password needs at least %s characters.") % MIN_PASSWORD)

        user.sudo().write({'password': new})
        # Odoo ends every other session on a password change; this one is kept
        # so she is not thrown out of the screen she just used. Signing out is
        # a link away if that is what she wanted.
        request.session.uid = user.id
        return request.redirect('/platform/account?saved=1')
