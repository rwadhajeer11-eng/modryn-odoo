import odoo
from odoo import _, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.web.controllers.home import Home

_lt = LazyTranslate(__name__)

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

    def _render(self, username='', phone='', error=None):
        return request.render('modryn_platform.platform_login', {
            'username': username,
            # The phone comes back so a mistyped digit somewhere else does not
            # cost her the whole form. The ID number and the password never do:
            # one is a national identity number and the other is a password,
            # and neither belongs in a rendered page a browser may cache.
            'phone': phone,
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

    # ONE sentence for every way of being wrong. A message that distinguishes
    # "no such user" from "wrong password" from "wrong ID number" hands whoever
    # is guessing a free answer at each step: first which usernames exist, then
    # which of the three secrets they have already got right. Four questions
    # asked together are only worth four if a refusal says nothing about any of
    # them.
    REFUSED = _lt("Something is wrong. Check what you entered and try again.")

    @http.route('/platform/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def platform_login_submit(self, **post):
        username = (post.get('username') or '').strip()
        phone = (post.get('phone') or '').strip()
        idnum = (post.get('idnum') or '').strip()
        password = post.get('password') or ''

        # Even "you left something blank" is not said field by field. The page
        # marks all four required, so an empty one is a browser being bypassed
        # rather than a person being helped.
        if not (username and phone and idnum and password):
            return self._render(username, phone, str(self.REFUSED))

        # The PASSWORD first, and the extra two after it. Order matters: Odoo
        # rate-limits and hashes password attempts, so checking it first means
        # the phone and the ID cannot be probed by anybody who does not already
        # have the password. Doing it the other way round would turn the two
        # additions into two things guessable for free.
        try:
            request.session.authenticate(
                request.env,
                {'login': username, 'password': password, 'type': 'password'})
        except odoo.exceptions.AccessDenied:
            return self._render(username, phone, str(self.REFUSED))

        # env.user is only refreshed once the environment is rebuilt for the new
        # session, so re-read it rather than trusting the pre-login value.
        user = request.env['res.users'].sudo().browse(request.session.uid)

        # Both remaining conditions, then one decision. Written as two variables
        # rather than two early returns so that a wrong group and a wrong ID
        # number leave by the same door at the same speed.
        allowed = user.has_group(GROUP_PLATFORM)
        factors_ok = user.modryn_check_platform_factors(phone, idnum)
        if not (allowed and factors_ok):
            # The session is destroyed either way. A correct password that
            # fails here must not leave a usable session behind.
            request.session.logout(keep_db=True)
            return self._render(username, phone, str(self.REFUSED))
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
    """Odoo's own sign-in stops being a second, easier door.

    /web/login asks two questions. The page beside it asks four, and two of
    those are only worth asking if there is nowhere to answer fewer — a phone
    number and an identity number protect nothing while a form that never asks
    for them is one URL away, bookmarked, and offered by Odoo itself to anyone
    who reaches a logged-out back-office address.

    This module is installed on the platform database and on no other, so this
    changes nothing for any boutique.

    IF THIS EVER LOCKS THE ACCOUNT OUT — which is the real risk of closing the
    only other door — `./odoo/odoo-bin shell -c odoo.conf -d platform` reaches
    the record with no login at all, and scripts/platform_factors.py sets both
    answers again in one command.
    """

    @http.route()
    def web_login(self, redirect=None, **kw):
        # A GET is somebody arriving. Send her to the door that asks properly,
        # rather than showing a form that will refuse her at the end.
        if request.httprequest.method == 'GET':
            return request.redirect('/platform/login')

        response = super().web_login(redirect=redirect, **kw)

        # A POST may still have authenticated somebody — a saved form, a script,
        # a password manager submitting straight to the endpoint. If that
        # somebody is the platform owner, the session is destroyed: she answered
        # two questions, and this account needs four.
        if request.session.uid:
            user = request.env['res.users'].sudo().browse(request.session.uid)
            if user.has_group(GROUP_PLATFORM):
                request.session.logout(keep_db=True)
                return request.redirect('/platform/login')
        return response
