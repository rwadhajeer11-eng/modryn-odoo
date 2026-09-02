from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request
from psycopg2 import IntegrityError

from odoo.tools import mute_logger

GROUP_PLATFORM = 'modryn_platform.group_platform_owner'



class ModrynPlatform(http.Controller):
    """MODRYN's own register of the boutiques that subscribe to it.

    Every route re-checks the group server-side. This database is not a
    boutique's, so there is no boutique staff to accidentally admit — but a
    hidden link has never been a permission and is not one here either.
    """

    def _is_platform_owner(self):
        user = request.env.user
        return not user._is_public() and user.has_group(GROUP_PLATFORM)

    @staticmethod
    def _list_values(form):
        """Partners and sign-ins, as the form posted them.

        getlist, because a repeated field name is how an HTML form says "several
        of these" - the idiom modryn_staff's role-page matrix already uses.

        REWRITTEN WHOLE, not diffed: a form that posts the list it was shown is
        describing the shop as it should now be. A diff would need ids in the
        page and a rule for every row that came back missing.

        ONLY WHEN THE FORM CARRIED THE LIST. The tier dropdown on the row posts
        to the edit route with nothing else in it, and reading its silence as
        "no partners" would empty the shop on a subscription change. Measured:
        six partners saved, then the dropdown alone saved, and the six stayed.
        """
        values = {}
        if 'partner_name' in form:
            names = form.getlist('partner_name')
            phones = form.getlist('partner_phone')
            values['partner_ids'] = [(5, 0, 0)] + [
                (0, 0, {'name': name.strip(),
                        'phone': (phones[i] if i < len(phones) else '').strip(),
                        'sequence': i * 10})
                for i, name in enumerate(names) if name.strip()]
        if 'account_username' in form:
            users = form.getlist('account_username')
            words = form.getlist('account_password')
            holders = form.getlist('account_holder')
            # A username is what makes a row a row. A password with no username
            # beside it is not a sign-in; it would sit in the list as a secret
            # belonging to nobody.
            values['account_ids'] = [(5, 0, 0)] + [
                (0, 0, {'username': user.strip(),
                        'password': (words[i] if i < len(words) else '').strip(),
                        'holder': (holders[i] if i < len(holders) else '').strip(),
                        'sequence': i * 10})
                for i, user in enumerate(users) if user.strip()]
        return values

    def _shops(self, archived=False):
        """The live register, or the archive. Never both in one list.

        They were one list with the archived rows greyed out, which reads as a
        register of forty shops when eight of them left last year.
        """
        return request.env['modryn.boutique'].sudo().with_context(
            active_test=False).search([('active', '=', not archived)])

    def _render(self, error=None):
        Type = request.env['modryn.subscription.type'].sudo()
        return request.render('modryn_platform.boutique_register', {
            'title': _("Boutiques"),
            'shops': [s._row() for s in self._shops()],
            # Counted as well as listed: the disclosure says how many are
            # inside it, so nobody has to open it to find out it is empty.
            'archived': [s._row() for s in self._shops(archived=True)],
            'types': Type.search([]),
            'error': error,
        })

    def _figures(self):
        """The four numbers, counted rather than guessed.

        An empty platform answers zero and the screen offers the door to add
        the first shop — a dash or a blank would read as the page being broken
        on the one day it is most likely to be opened.
        """
        Shop = request.env['modryn.boutique'].sudo()
        Type = request.env['modryn.subscription.type'].sudo()
        Feature = request.env['modryn.platform.feature'].sudo()
        Partner = request.env['modryn.boutique.partner'].sudo()
        return {
            'shops': Shop.search_count([]),
            'tiers': Type.search_count([]),
            'features': Feature.search_count([]),
            'partners': Partner.search_count([]),
            # Shops on no tier at all. Named on the screen as a job to do
            # rather than as a statistic: those are shops nobody is billing.
            'untiered': Shop.search_count([('subscription_type_id', '=', False)]),
        }

    def _by_tier(self):
        """Which boutiques sit on which tier, biggest first.

        Only tiers that hold somebody. A list of four empty rows answers
        nothing, and the tiers themselves are one screen away.
        """
        rows = []
        for kind in request.env['modryn.subscription.type'].sudo().search([]):
            shops = request.env['modryn.boutique'].sudo().search(
                [('subscription_type_id', '=', kind.id)])
            if not shops:
                continue
            rows.append({
                'id': kind.id,
                'name': kind.name or '',
                'count': len(shops),
                'shops': ' · '.join(shops.mapped('name')),
            })
        rows.sort(key=lambda r: r['count'], reverse=True)
        return rows

    @http.route('/platform/home', type='http', auth='user', website=True,
                sitemap=False)
    def platform_home(self, **kw):
        if not self._is_platform_owner():
            return request.not_found()
        figures = self._figures()
        # Built whole, with the count inside it. Split around a t-out it
        # exported as "boutique(s) are on no subscription at all." with the
        # number outside — a fragment nobody can place, since in Hebrew and
        # Arabic the number does not sit where English puts it.
        figures['untiered_says'] = _(
            "%(count)s boutique(s) are on no subscription at all."
        ) % {'count': figures['untiered']}
        return request.render('modryn_platform.platform_home', {
            'here': 'home',
            # The browser tab, translated. Odoo would otherwise take it from
            # the template's `name`, which is an ir.ui.view varchar that no
            # .po can reach.
            'title': _("Your platform"),
            'figures': figures,
            'by_tier': self._by_tier(),
        })

    @http.route('/platform/boutiques', type='http', auth='user', website=True,
                sitemap=False)
    def boutiques(self, error=None, **kw):
        if not self._is_platform_owner():
            return request.not_found()
        return self._render(error=error)

    @http.route('/platform/boutiques/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def boutique_new(self, **post):
        if not self._is_platform_owner():
            return request.not_found()

        code = (post.get('code') or '').strip()
        name = (post.get('name') or '').strip()
        if not code.isdigit() or len(code) != 4:
            return request.redirect(
                '/platform/boutiques?error=%s' % _("A shop number is exactly four digits"))
        if not name:
            return request.redirect(
                '/platform/boutiques?error=%s' % _("Please enter the shop's name"))

        values = {
            'code': code,
            'name': name,
            'city': (post.get('city') or '').strip(),
            'street': (post.get('street') or '').strip(),
            'slug': (post.get('slug') or '').strip(),
        }
        type_id = post.get('subscription_type_id')
        if type_id:
            values['subscription_type_id'] = int(type_id)

        # Partners and sign-ins, through the same reader the edit form uses:
        # one place that knows how a repeated field name becomes a list.
        values.update(self._list_values(request.httprequest.form))

        # Savepoint: a constraint the model enforces has to come back as a
        # sentence rather than poisoning the request's transaction.
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                request.env['modryn.boutique'].sudo().create(values)
        except (ValidationError, IntegrityError):
            return request.redirect(
                '/platform/boutiques?error=%s' % _("That shop number is already taken"))
        return request.redirect('/platform/boutiques')

    @http.route('/platform/boutiques/edit/<int:shop_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def boutique_edit(self, shop_id, **post):
        if not self._is_platform_owner():
            return request.not_found()
        shop = request.env['modryn.boutique'].sudo().with_context(
            active_test=False).browse(shop_id).exists()
        if not shop:
            return request.redirect('/platform/boutiques')

        values = {}
        # `code` joins the list: a shop number typed wrong on the day it was
        # added could not be corrected here at all, and it is the one field
        # every other system refers to the shop by.
        for field in ('code', 'name', 'city', 'street', 'slug', 'note'):
            if field in post:
                values[field] = (post.get(field) or '').strip()
        if post.get('subscription_type_id'):
            values['subscription_type_id'] = int(post['subscription_type_id'])
        elif 'subscription_type_id' in post:
            values['subscription_type_id'] = False

        values.update(self._list_values(request.httprequest.form))
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                shop.write(values)
        except (ValidationError, IntegrityError):
            return request.redirect(
                '/platform/boutiques?error=%s' % _("Those details aren't valid"))
        return request.redirect('/platform/boutiques')

    @http.route('/platform/boutiques/archive/<int:shop_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def boutique_archive(self, shop_id, **post):
        if not self._is_platform_owner():
            return request.not_found()
        shop = request.env['modryn.boutique'].sudo().with_context(
            active_test=False).browse(shop_id).exists()
        if shop:
            # Archive, never delete. A shop that leaves is history MODRYN still
            # bills against, and unlink would take its partners with it.
            shop.active = not shop.active
        return request.redirect('/platform/boutiques')

    @http.route('/platform/boutiques/delete/<int:shop_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def boutique_delete(self, shop_id, **post):
        """Remove a boutique from the register, for good.

        WHAT THIS DELETES, and the screen says so too: the platform's ROW about
        the shop. Not the shop's database, not its dresses, not its brides —
        those live in their own database, which this module has never been able
        to reach. What goes is MODRYN's record that the shop was a customer.

        ALL FOUR ANSWERS, the same ones the door asks. Archiving is reversible
        and asks for a confirmation; this is not reversible and asks for proof.

        A WRONG ANSWER SAYS NOTHING. Not which field, not "wrong password",
        not "that shop is gone already" — the row simply stays. Anything more
        specific is a free answer for somebody sitting at a screen its owner
        walked away from.
        """
        if not self._is_platform_owner():
            return request.not_found()

        user = request.env.user
        ok = user.modryn_platform_credentials_ok(
            login=post.get('username') or '',
            phone=post.get('phone') or '',
            idnum=post.get('idnum') or '',
            password=post.get('password') or '',
        )
        if not ok:
            return request.redirect(
                '/platform/boutiques?error=%s#archive' % _("Those details aren't valid"))

        shop = request.env['modryn.boutique'].sudo().with_context(
            active_test=False).browse(shop_id).exists()
        # Archived only. A live shop is deleted by archiving it first, which is
        # a second deliberate act — and it means a mistyped id in a hand-made
        # POST cannot take out a shop that is still trading.
        if shop and not shop.active:
            shop.unlink()
        return request.redirect('/platform/boutiques#archive')

    # ------------------------------------------------------ plans and features
    def _plans_context(self, error=None):
        Type = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False)
        Feature = request.env['modryn.platform.feature'].sudo().with_context(
            active_test=False)
        return {
            'title': _("Subscriptions"),
            'types': Type.search([]),
            'features': Feature.search([]),
            # The catalogue of what can be sold, in the order a boutique would
            # meet the screens in.
            'screens': request.env['modryn.platform.screen'].sudo().search([]),
            'error': error,
        }

    @staticmethod
    def _money(raw):
        """A price out of a form field.

        Never negative: a tier that pays the boutique is a typo, and storing it
        would put a minus sign on somebody's invoice. Never a crash either - the
        number field is a hint to the browser, not a promise about what arrives.
        """
        try:
            return max(float(raw or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _ticked(form, field):
        """The ids a tick-list posted, as ints.

        A checkbox only posts when it is ticked, so an absent name means "none
        of them" - exactly right for a form that renders every box it could
        tick. The digit check is not politeness: an id arriving as 'banana' is
        somebody hand-making a POST, and int() would answer them with a 500.
        """
        return [int(v) for v in form.getlist(field) if str(v).isdigit()]

    @http.route('/platform/plans', type='http', auth='user', website=True,
                sitemap=False)
    def plans(self, error=None, **kw):
        if not self._is_platform_owner():
            return request.not_found()
        return request.render('modryn_platform.plans', self._plans_context(error))

    @http.route('/platform/plans/feature/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def feature_new(self, **post):
        """A feature the owner has just decided exists."""
        if not self._is_platform_owner():
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/platform/plans?error=%s' % _("Please name the feature"))
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                request.env['modryn.platform.feature'].sudo().create({
                    'name': name,
                    'code': (post.get('code') or '').strip(),
                    'note': (post.get('note') or '').strip(),
                })
        except (ValidationError, IntegrityError):
            return request.redirect(
                '/platform/plans?error=%s' % _("That feature already exists"))
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/feature/archive/<int:feature_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True, sitemap=False)
    def feature_archive(self, feature_id, **post):
        if not self._is_platform_owner():
            return request.not_found()
        feature = request.env['modryn.platform.feature'].sudo().with_context(
            active_test=False).browse(feature_id).exists()
        if feature:
            # Archive, never delete: tiers point at it, and unlinking would
            # silently strip the feature out of every plan that included it.
            feature.active = not feature.active
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/type/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def plan_new(self, **post):
        if not self._is_platform_owner():
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect('/platform/plans?error=%s' % _("Please name the plan"))
        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                request.env['modryn.subscription.type'].sudo().create({
                    'name': name,
                    'note': (post.get('note') or '').strip(),
                    'price': self._money(post.get('price')),
                })
        except (ValidationError, IntegrityError):
            return request.redirect(
                '/platform/plans?error=%s' % _("That subscription type already exists"))
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/type/edit/<int:type_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def plan_type_edit(self, type_id, **post):
        """Rename a tier, price it, and set what it sells.

        ONE ROUTE FOR ALL OF IT, because the tier's form posts as one: a price
        typed beside a tick that did not save is worse than either failing
        alone.
        """
        if not self._is_platform_owner():
            return request.not_found()
        tier = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False).browse(type_id).exists()
        if not tier:
            return request.redirect('/platform/plans')

        values = {}
        if 'name' in post:
            name = (post.get('name') or '').strip()
            if not name:
                return request.redirect(
                    '/platform/plans?error=%s' % _("Please name the plan"))
            values['name'] = name
        if 'note' in post:
            values['note'] = (post.get('note') or '').strip()
        if 'price' in post:
            values['price'] = self._money(post.get('price'))

        # `ticks_posted` is a hidden field the tick form carries. Without it an
        # empty list is ambiguous - it could mean "untick everything" or "this
        # form was not about ticks at all" - and reading the second as the first
        # would strip a tier's screens every time its price was corrected.
        form = request.httprequest.form
        if 'ticks_posted' in form:
            values['screen_ids'] = [(6, 0, self._ticked(form, 'screen_id'))]
            values['section_ids'] = [(6, 0, self._ticked(form, 'section_id'))]
            values['feature_ids'] = [(6, 0, self._ticked(form, 'feature_id'))]

        try:
            with request.env.cr.savepoint(), mute_logger('odoo.sql_db'):
                tier.write(values)
                # A box he left ticked under a screen he has just unticked.
                tier.modryn_drop_orphan_sections()
        except (ValidationError, IntegrityError):
            return request.redirect('/platform/plans?error=%s'
                                    % _("That subscription type already exists"))
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/type/delete/<int:type_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def plan_type_delete(self, type_id, **post):
        """Remove a tier for good, on all four sign-in answers.

        The same bar as deleting a boutique, and for the same reason: it cannot
        be undone. A wrong answer removes nothing and says nothing about which.

        A TIER SOMEBODY IS ON IS REFUSED, and this refusal DOES say why - it is
        not a credential question, and the owner needs to know. Deleting it
        would leave those shops on a subscription that no longer exists, which
        the home screen then reports as shops nobody is billing.
        """
        if not self._is_platform_owner():
            return request.not_found()

        if not request.env.user.modryn_platform_credentials_ok(
                login=post.get('username') or '',
                phone=post.get('phone') or '',
                idnum=post.get('idnum') or '',
                password=post.get('password') or ''):
            return request.redirect(
                '/platform/plans?error=%s' % _("Those details aren't valid"))

        tier = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False).browse(type_id).exists()
        if not tier:
            return request.redirect('/platform/plans')
        if request.env['modryn.boutique'].sudo().with_context(
                active_test=False).search_count(
                    [('subscription_type_id', '=', tier.id)]):
            return request.redirect('/platform/plans?error=%s' % _(
                "Boutiques are still on that subscription."))
        tier.unlink()
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/type/archive/<int:type_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def plan_archive(self, type_id, **post):
        if not self._is_platform_owner():
            return request.not_found()
        tier = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False).browse(type_id).exists()
        if tier:
            # Archive: boutiques point at this tier, and deleting it would blank
            # the subscription on every one of them without saying so.
            tier.active = not tier.active
        return request.redirect('/platform/plans')
