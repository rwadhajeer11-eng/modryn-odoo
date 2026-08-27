import odoo
from odoo import _, http
from odoo.http import request

# Where each level lands after signing in. The owner configures, the manager
# runs the room, and plain staff get their own day — not the whole floor.
LANDING_OWNER = '/manage/staff'
LANDING_FLOOR = '/floor'
LANDING_HOME = '/staff/home'


def landing_for(user):
    if user.has_group('modryn_staff.group_boutique_owner'):
        return LANDING_OWNER
    if user.has_group('modryn_staff.group_shift_manager'):
        return LANDING_FLOOR
    return LANDING_HOME


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



    # ----------------------------------------------------------------- the bell
    @http.route('/staff/notifications', type='http', auth='user', website=True,
                sitemap=False)
    def notifications(self, **kw):
        """Everything she has been told, newest first.

        Opening the page marks it read - the bell is a "there is news" light,
        not an inbox with per-item state. A tick-each-one flow would mean a
        count that only ever goes down when somebody remembers to press
        something.
        """
        if not self._is_staff():
            return request.not_found()
        me = self._my_employee()
        if not me:
            return request.not_found()
        Notification = request.env['modryn.staff.notification'].sudo()
        rows = Notification.search([('employee_id', '=', me.id)], limit=100)
        unread = rows.filtered(lambda n: not n.read_at)
        page = request.render('modryn_staff.staff_notifications', {
            'rows': rows,
            'unread_ids': unread.ids,
            'active_tab': 'notifications',
        })
        # AFTER rendering, so the page she is looking at still shows which ones
        # were new. Reading it is what marks them.
        unread.modryn_mark_read()
        return page

    # ------------------------------------------------------------- her profile
    # Same shape as staff_lang above, and for the same reason: portal users have
    # no ORM access to hr.employee, so the group is checked HERE and the write
    # goes through sudo() - which makes it critical that the record is resolved
    # from the SESSION and never from anything the request carries. There is no
    # employee id in these routes at all, so there is no id to tamper with.
    #
    # WHAT SHE MAY CHANGE is deliberately narrower than the owner's form: her
    # name, how to reach her, where she lives, how she is addressed and which
    # language she reads. NOT her role, NOT her permission level, and NOT her ID
    # number - those are the owner's record of her, not her description of
    # herself, and letting her edit them would make the Team page advisory.
    HER_OWN = ('city', 'street', 'backup_phone', 'gender')

    def _is_staff(self):
        """Boutique staff, and not the public user.

        The same check staff_lang makes inline. Written out here because these
        routes write to hr.employee through sudo(), and a sudo() write behind a
        check that is easy to forget is how a public visitor edits somebody's
        record.
        """
        user = request.env.user
        return not user._is_public() and user.has_group(
            'modryn_staff.group_boutique_staff')

    def _my_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _profile_context(self, employee, saved=False, error=None):
        genders = request.env['hr.employee']._fields['modryn_gender'].get_description(
            request.env)['selection']
        return {
            'employee': employee,
            'genders': genders,
            'langs': self._staff_langs(),
            'saved': saved,
            'error': error,
            'active_tab': 'profile',
        }

    @http.route('/staff/profile', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def profile_form(self, saved=None, **kw):
        if not self._is_staff():
            return request.not_found()
        me = self._my_employee()
        if not me:
            return request.not_found()
        request.session.touch()
        return request.render('modryn_staff.staff_profile',
                              self._profile_context(me, saved=bool(saved)))

    @http.route('/staff/profile', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def profile_save(self, **post):
        if not self._is_staff():
            return request.not_found()
        me = self._my_employee()
        if not me:
            return request.not_found()

        name = (post.get('name') or '').strip()
        if not name:
            return request.render('modryn_staff.staff_profile', self._profile_context(
                me, error=_("Please enter your name.")))

        values = {'name': name, 'work_phone': (post.get('phone') or '').strip()}
        for field in self.HER_OWN:
            if field == 'gender':
                # Validated against the field's OWN selection, not a list copied
                # here: a stored value outside it renders as nothing at all, so
                # the form would silently blank her answer.
                valid = dict(request.env['hr.employee']._fields[
                    'modryn_gender'].get_description(request.env)['selection'])
                raw = (post.get('gender') or '').strip()
                values['modryn_gender'] = raw if raw in valid else False
            else:
                values['modryn_%s' % field] = (post.get(field) or '').strip()
        me.write(values)

        # Language rides the existing picker's rules rather than a second copy:
        # it is a whitelist of the tenant's switched-on languages, and writing a
        # code the boutique never activated leaves her with a preference that
        # renders nothing.
        lang = (post.get('lang') or '').strip()
        if lang and self._staff_langs().filtered(lambda l: l.code == lang):
            request.env.user.sudo().lang = lang

        return request.redirect('/staff/profile?saved=1')

    # Was a hardcoded {'he_IL', 'en_US'}. It is STILL a whitelist — a portal user
    # writes her own lang through sudo() below, and an unchecked value would let
    # her set any field-legal string — but the list is now the tenant's own
    # switched-on languages, so an owner enabling Arabic or Russian gets it in
    # the picker with no code change. search([]) is already active-only.
    def _staff_langs(self):
        return request.env['res.lang'].sudo().search([])

    @http.route('/staff/lang', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def staff_lang(self, lang=None, redirect=None, **post):
        user = request.env.user
        if user._is_public() or not user.has_group('modryn_staff.group_boutique_staff'):
            return request.not_found()
        langs = self._staff_langs()
        target = langs.filtered(lambda l: l.code == lang)[:1]
        if not target:
            return request.redirect(redirect or '/floor')

        # Two writes, because "the user's language" is two different things:
        # user.lang is her stored preference, but a WEBSITE page renders in the
        # URL's language — writing the preference alone changes nothing visible
        # (verified: the partner switched to en_US and the page stayed Hebrew).
        # So also route her through the language-prefixed URL and pin the
        # frontend_lang cookie, which is how the website remembers a visitor.
        user.sudo().lang = target.code

        path = redirect or '/floor'
        # Strip whatever language prefix the path already carries. Derived from
        # the tenant's own languages, not a hardcoded ('/en', '/ar'): switch a
        # third language on and that list leaves its prefix in place, so the
        # redirect lands back in the language she just left. Longest first, so a
        # short code can never shadow a longer one that starts with it.
        for code in sorted(langs.mapped('url_code'), key=len, reverse=True):
            if path == '/' + code or path.startswith('/' + code + '/'):
                path = path[len(code) + 1:] or '/'
                break
        # Odoo serves the website's DEFAULT language with no prefix at all —
        # prefixing that one 404s.
        default_lang = request.env['website'].get_current_website().sudo().default_lang_id
        if target.url_code != default_lang.url_code:
            path = '/' + target.url_code + (path if path.startswith('/') else '/' + path)

        response = request.redirect(path)
        response.set_cookie('frontend_lang', target.code)
        return response
