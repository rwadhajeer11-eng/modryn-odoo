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
            # Everybody who may open the platform. Read here rather than in
            # the template so the "is this me" flag is decided once.
            'others': [{
                'id': other.id,
                'login': other.login or '',
                'name': other.name or '',
                'is_me': other.id == user.id,
                'ready': bool(other.sudo().read(
                    ['modryn_platform_phone', 'modryn_platform_idnum'])[0]
                    .get('modryn_platform_phone')),
            } for other in request.env['res.users'].sudo().search(
                [('group_ids', 'in', request.env.ref(
                    GROUP_PLATFORM).ids)], order='login')],
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

    @staticmethod
    def _keep_my_session():
        """Stay signed in after changing your own password.

        A password change invalidates every session that account has, which is
        the point of it - but that includes THIS one, and being thrown to the
        login page by the screen you just used reads as the change having
        failed. Setting session.uid is not enough and was the bug here: the
        session also carries a token derived from the password, and it is the
        token the next request is checked against. Recomputed exactly as Odoo's
        own portal does it after the same operation.
        """
        request.session.session_token = request.env.user._compute_session_token(
            request.session.sid)

    def _four_answers_ok(self, post):
        """All four sign-in answers, checked as the door checks them.

        THE BAR FOR EVERY CHANGE ON THIS SCREEN THAT TOUCHES WHO CAN GET IN:
        my own answers, my password, adding a person, editing one, removing
        one. A password on its own used to be enough, and that made the two
        extra factors weaker than having none - anybody reaching a session left
        open could rewrite the very answers that keep them out, using the one
        answer they had already got past.

        The fields are named `auth_*` rather than the bare names the other
        screens use, because this screen has forms carrying BOTH a username to
        prove and a username to set, and one name for two meanings is how a
        form starts saving the wrong one.

        Never short-circuited: modryn_platform_credentials_ok checks all four
        before answering, so a wrong one costs the same time as a right one.
        """
        return request.env.user.modryn_platform_credentials_ok(
            login=post.get('auth_username') or '',
            phone=post.get('auth_phone') or '',
            idnum=post.get('auth_idnum') or '',
            password=post.get('auth_password') or '')

    @http.route('/platform/account/login', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def account_login(self, **post):
        user = self._owner()
        if not user:
            return request.not_found()

        # ALL FOUR, not the password alone. See _four_answers_ok.
        if not self._four_answers_ok(post):
            return self._render(error=_("Those details aren't valid"))

        login = (post.get('new_username') or '').strip()
        if not login:
            return self._render(error=_("You need a username."))

        phone = (post.get('new_phone') or '').strip()
        idnum = (post.get('new_idnum') or '').strip()

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

        if not self._four_answers_ok(post):
            return self._render(error=_("Those details aren't valid"))

        new = post.get('new_password') or ''
        again = post.get('new_password2') or ''
        if new != again:
            return self._render(error=_("The two new passwords are not the same."))
        if len(new) < MIN_PASSWORD:
            return self._render(error=_(
                "A password needs at least %s characters.") % MIN_PASSWORD)

        user.sudo().write({'password': new})
        self._keep_my_session()
        return request.redirect('/platform/account?saved=1')

    @http.route('/platform/account/user/new', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def account_user_new(self, **post):
        """Another person who may run the platform.

        Created with all four answers at once, because an account that exists
        with only two of them is an account that cannot sign in - the door
        refuses a user with nothing on file, deliberately - and would sit in the
        list looking finished.
        """
        me = self._owner()
        if not me:
            return request.not_found()
        if not self._four_answers_ok(post):
            return self._render(error=_("Those details aren't valid"))

        login = (post.get('new_login') or '').strip()
        name = (post.get('new_name') or '').strip() or login
        phone = (post.get('new_phone') or '').strip()
        idnum = (post.get('new_idnum') or '').strip()
        password = post.get('new_user_password') or ''

        if not (login and phone and idnum and password):
            return self._render(error=_(
                "A new person needs a username, a phone number, an ID number "
                "and a password."))
        if len(password) < MIN_PASSWORD:
            return self._render(error=_(
                "A password needs at least %s characters.") % MIN_PASSWORD)
        if request.env['res.users'].sudo().search_count([('login', '=', login)]):
            return self._render(error=_("That username is taken."))

        created = request.env['res.users'].sudo().create({
            'name': name,
            'login': login,
            'password': password,
            'company_id': me.company_id.id,
            'company_ids': [(4, me.company_id.id)],
            'group_ids': [(4, request.env.ref(GROUP_PLATFORM).id)],
        })
        created.modryn_set_platform_factors(phone=phone, idnum=idnum)
        return request.redirect('/platform/account?saved=1#people')

    @http.route('/platform/account/user/edit/<int:user_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def account_user_edit(self, user_id, **post):
        """Correct somebody's username, name, or any of their four answers.

        The list could add people and remove them and nothing in between, so a
        mistyped phone number meant deleting the person and making them again -
        which is a different thing from correcting them, and reads as one on
        any screen that lists who was removed.

        BLANK MEANS LEAVE IT for the phone, the identity number and the
        password. All three are stored as hashes and cannot be shown back, so
        the form has nothing to put in the box; treating an empty field as
        "erase" would lock somebody out on the next save of their name.

        ANY ACCOUNT MAY EDIT ANY OTHER, and no account is the main one. The
        four answers asked for are the answers of whoever is doing it.
        """
        me = self._owner()
        if not me:
            return request.not_found()
        if not self._four_answers_ok(post):
            return self._render(error=_("Those details aren't valid"))

        target = request.env['res.users'].sudo().browse(user_id).exists()
        if not target or not target.has_group(GROUP_PLATFORM):
            return request.redirect('/platform/account#people')

        login = (post.get('login') or '').strip()
        if not login:
            return self._render(error=_("You need a username."))
        if login != target.login and request.env['res.users'].sudo().search_count(
                [('login', '=', login), ('id', '!=', target.id)]):
            return self._render(error=_("That username is taken."))

        password = post.get('password') or ''
        if password and len(password) < MIN_PASSWORD:
            return self._render(error=_(
                "A password needs at least %s characters.") % MIN_PASSWORD)

        values = {'login': login,
                  'name': (post.get('name') or '').strip() or login}
        if password:
            values['password'] = password
        target.write(values)

        phone = (post.get('phone') or '').strip()
        idnum = (post.get('idnum') or '').strip()
        if phone or idnum:
            target.modryn_set_platform_factors(
                phone=phone or None, idnum=idnum or None)

        # The list offers no Edit against your own row, but a POST can still
        # name your own id, and changing your own password there ends your
        # session in exactly the same way.
        if target.id == me.id and password:
            self._keep_my_session()
        return request.redirect('/platform/account?saved=1#people')

    @http.route('/platform/account/user/delete/<int:user_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def account_user_delete(self, user_id, **post):
        """Remove somebody, behind all four answers.

        A WRONG ANSWER SAYS NOTHING about which one, the same silence the door
        keeps and the boutique delete keeps.

        SELF-DELETION IS REFUSED, and not because of permissions: it is the one
        mistake that cannot be undone from the screen that made it, since the
        account doing the undoing would be the account that just went.

        NOBODY IS THE MAIN ACCOUNT. Anyone here can remove anyone else,
        including whoever made them, and the four answers asked for are the
        answers of whoever is doing it.

        WHAT "REMOVE" DOES depends on what the row is, and the difference is
        not about rank. A person added on this screen is a row that exists for
        this screen, and it is unlinked. `admin` is not: on this database it is
        Odoo's OWN administrator record, pointed at by ir_model_data and by
        half the modules installed, and unlinking it would not remove a person
        from the platform - it would break the database they were removed from.
        So for a row Odoo owns, removing means TAKING ITS PLATFORM ACCESS AWAY.
        The result on this screen is identical: the row leaves the list and
        that account can no longer sign in.
        """
        me = self._owner()
        if not me:
            return request.not_found()

        if not self._four_answers_ok(post):
            return self._render(error=_("Those details aren't valid"))

        target = request.env['res.users'].sudo().browse(user_id).exists()
        if not target or target.id == me.id or not target.has_group(GROUP_PLATFORM):
            return request.redirect('/platform/account?saved=1#people')

        # Does Odoo itself own this row? An xml id is the plain answer: rows
        # created on this screen have none, and base.user_admin has one.
        owned_by_odoo = request.env['ir.model.data'].sudo().search_count(
            [('model', '=', 'res.users'), ('res_id', '=', target.id)])
        if owned_by_odoo:
            target.write({'group_ids': [(3, request.env.ref(GROUP_PLATFORM).id)]})
        else:
            target.unlink()
        return request.redirect('/platform/account?saved=1#people')
