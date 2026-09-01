import odoo
from odoo import _, http
from odoo.http import request

from odoo.addons.web.controllers.home import Home

GROUP_PLATFORM = 'modryn_platform.group_platform_owner'

# Where the platform owner belongs the moment she is signed in. Her register,
# not Odoo's back office: this database exists to answer one question — which
# boutiques subscribe, and on what — and everything else Odoo puts on that
# screen is furniture from a different product.
LANDING = '/platform/boutiques'


class ModrynPlatformAuth(http.Controller):
    """A sign-in for the platform owner that looks nothing like Odoo.

    The same argument modryn_staff makes for the boutiques, and the same shape,
    deliberately — but a separate copy, because the two never share a database
    and this module does not depend on that one.

    What it replaces, measured rather than assumed: signing in at /web/login
    landed on Odoo's Discuss — a chat app with a robot in the sidebar — and the
    register could only be reached by typing its address by hand. The page
    before that carried "Your Logo", a stock paragraph about disruptive
    products, and "Powered by odoo" in the footer.

    It does NOT reimplement authentication. That would mean touching password
    hashing and getting it wrong quietly; it calls the same
    session.authenticate() the stock controller calls.
    """

    def _render(self, username='', error=None):
        return request.render('modryn_platform.platform_login', {
            'username': username,
            'error': error,
        })

    @http.route('/platform/login', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def platform_login_form(self, **kw):
        user = request.env.user
        if user and not user._is_public() and user.has_group(GROUP_PLATFORM):
            return request.redirect(LANDING)
        # The CSRF token is an HMAC over session.sid, and Odoo only sends a
        # session cookie once the session is dirty — rendering a page does not
        # dirty it. Without this, a visitor whose FIRST request is this page
        # posts under a brand-new sid and is rejected with a bare 400. The
        # staff login carries the same line for the same reason.
        request.session.touch()
        return self._render()

    @http.route('/platform/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def platform_login_submit(self, **post):
        username = (post.get('username') or '').strip()
        password = post.get('password') or ''

        if not username or not password:
            return self._render(username, _("Enter your username and password."))

        try:
            request.session.authenticate(
                request.env,
                {'login': username, 'password': password, 'type': 'password'})
        except odoo.exceptions.AccessDenied:
            # One message for both wrong-user and wrong-password: saying which
            # was wrong tells whoever is guessing which usernames exist. The
            # password is never echoed back into the form.
            return self._render(username, _("Incorrect username or password."))

        # env.user is only refreshed once the environment is rebuilt for the new
        # session, so re-read it rather than trusting the pre-login value.
        user = request.env['res.users'].sudo().browse(request.session.uid)
        if not user.has_group(GROUP_PLATFORM):
            # A valid Odoo account that is not the platform owner has no
            # business on the register. Logged straight back out rather than
            # left holding a session that opens nothing.
            request.session.logout(keep_db=True)
            return self._render(
                username, _("This account isn't allowed into the platform."))
        return request.redirect(LANDING)

    @http.route('/platform/logout', type='http', auth='public', website=True,
                sitemap=False)
    def platform_logout(self, **kw):
        # keep_db: the database is resolved from the hostname, and dropping it
        # would bounce her to Odoo's database selector instead of this login.
        request.session.logout(keep_db=True)
        return request.redirect('/platform/login')

    @http.route('/platform', type='http', auth='public', website=True,
                sitemap=False)
    def platform_root(self, **kw):
        """The bare address, so /platform is a thing somebody can type."""
        user = request.env.user
        if user and not user._is_public() and user.has_group(GROUP_PLATFORM):
            return request.redirect(LANDING)
        return request.redirect('/platform/login')


class ModrynPlatformHome(Home):
    """Odoo's own sign-in also lands the platform owner on her register.

    The new page above is the door she is given, but /web/login still exists —
    it is bookmarked, it is what a password manager saved, and it is where Odoo
    sends anyone who reaches a logged-out back-office URL. Sending her to
    Discuss from THERE and to the register from here would be two answers to
    one question.

    Only for the platform owner. Anybody else signing in at /web/login keeps
    Odoo's own behaviour, because on this database that is nobody in normal
    use, and quietly redirecting a support session away from the back office is
    how somebody loses the ability to fix things.
    """

    def _login_redirect(self, uid, redirect=None):
        # An explicit ?redirect= is the caller asking for somewhere specific;
        # it wins, the same way it does upstream.
        if not redirect:
            user = request.env['res.users'].sudo().browse(uid)
            if user.has_group(GROUP_PLATFORM):
                return LANDING
        return super()._login_redirect(uid, redirect=redirect)
