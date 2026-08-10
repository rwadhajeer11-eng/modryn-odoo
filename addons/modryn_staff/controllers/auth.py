import odoo
from odoo import _, http
from odoo.http import request

# Where each level lands after signing in. The owner configures, everyone else works.
LANDING_OWNER = '/manage/staff'
LANDING_FLOOR = '/floor'


def landing_for(user):
    if user.has_group('modryn_staff.group_boutique_owner'):
        return LANDING_OWNER
    return LANDING_FLOOR


class ModrynStaffAuth(http.Controller):
    """A staff sign-in that looks nothing like Odoo.

    Deliberately not a skin over /web/login: the owner's requirement was that
    staff never meet Odoo's login page or its vocabulary. So this asks for a
    *username*, not an email, and lives under /staff.

    It does NOT reimplement authentication — that would mean touching password
    hashing, and getting it wrong quietly. It calls the same
    session.authenticate() the stock controller calls.
    """

    def _render_login(self, username='', error=None, redirect=None):
        return request.render('modryn_staff.staff_login', {
            'username': username,
            'error': error,
            'redirect': redirect or '',
        })

    @http.route('/staff/login', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def staff_login_form(self, redirect=None, **kw):
        if request.env.user and not request.env.user._is_public():
            return request.redirect(redirect or landing_for(request.env.user))

        # The CSRF token is an HMAC over session.sid, but Odoo only sends a
        # session cookie when the session is dirty — and simply rendering a page
        # does not dirty it. Without this, a visitor whose FIRST request is the
        # login page posts under a brand-new sid and is rejected with a bare 400.
        # Other pages hide the bug only because the visitor already had a cookie.
        request.session.touch()
        return self._render_login(redirect=redirect)

    @http.route('/staff/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def staff_login_submit(self, **post):
        username = (post.get('username') or '').strip()
        password = post.get('password') or ''
        redirect = post.get('redirect') or ''

        if not username or not password:
            return self._render_login(
                username, _("Enter your username and password."), redirect)

        credential = {'login': username, 'password': password, 'type': 'password'}
        try:
            request.session.authenticate(request.env, credential)
        except odoo.exceptions.AccessDenied:
            # One message for both wrong-user and wrong-password: saying which
            # was wrong tells an attacker which usernames exist.
            # The password is never echoed back into the form.
            return self._render_login(
                username, _("Incorrect username or password."), redirect)

        # env.user is only refreshed once the environment is rebuilt for the new
        # session, so re-read it rather than trusting the pre-login value.
        user = request.env['res.users'].sudo().browse(request.session.uid)
        if not user.has_group('modryn_staff.group_boutique_staff'):
            # A valid Odoo account that is not boutique staff (a portal customer,
            # say) must not land on the floor terminal.
            request.session.logout(keep_db=True)
            return self._render_login(
                username, _("This account isn't allowed into the staff system."), redirect)

        return request.redirect(redirect or landing_for(user))

    @http.route('/staff/logout', type='http', auth='public', website=True, sitemap=False)
    def staff_logout(self, **kw):
        # keep_db: the tenant is resolved from the hostname, and dropping it would
        # bounce the user to Odoo's database selector instead of our login page.
        request.session.logout(keep_db=True)
        return request.redirect('/staff/login')

    # Staff screens are Hebrew/English by product decision (customers get Arabic
    # too, on the storefront switcher). A hard whitelist, not free choice: a
    # portal user writes her own lang through sudo() here, and sudo plus an
    # unchecked value would let her set any field-legal language.
    STAFF_LANGS = {'he_IL', 'en_US'}

    @http.route('/staff/lang', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def staff_lang(self, lang=None, redirect=None, **post):
        user = request.env.user
        if user._is_public() or not user.has_group('modryn_staff.group_boutique_staff'):
            return request.not_found()
        if lang not in self.STAFF_LANGS:
            return request.redirect(redirect or '/floor')

        # Two writes, because "the user's language" is two different things:
        # user.lang is her stored preference, but a WEBSITE page renders in the
        # URL's language — writing the preference alone changes nothing visible
        # (verified: the partner switched to en_US and the page stayed Hebrew).
        # So also route her through the language-prefixed URL and pin the
        # frontend_lang cookie, which is how the website remembers a visitor.
        user.sudo().lang = lang

        path = redirect or '/floor'
        for code in ('/en', '/ar'):
            if path == code or path.startswith(code + '/'):
                path = path[len(code):] or '/'
                break
        if lang == 'en_US':
            path = '/en' + (path if path.startswith('/') else '/' + path)

        response = request.redirect(path)
        response.set_cookie('frontend_lang', lang)
        return response
