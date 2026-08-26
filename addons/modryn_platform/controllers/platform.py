from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request
from psycopg2 import IntegrityError

from odoo.tools import mute_logger

GROUP_PLATFORM = 'modryn_platform.group_platform_owner'

# Four partners per shop, matching the model's constraint. Checked here as well,
# so the owner reads a sentence instead of meeting a traceback — the same
# division of labour modryn_staff's manage controller uses.
MAX_PARTNERS = 4


class ModrynPlatform(http.Controller):
    """MODRYN's own register of the boutiques that subscribe to it.

    Every route re-checks the group server-side. This database is not a
    boutique's, so there is no boutique staff to accidentally admit — but a
    hidden link has never been a permission and is not one here either.
    """

    def _is_platform_owner(self):
        user = request.env.user
        return not user._is_public() and user.has_group(GROUP_PLATFORM)

    def _shops(self, **kw):
        return request.env['modryn.boutique'].sudo().with_context(
            active_test=False).search([])

    def _render(self, error=None):
        Type = request.env['modryn.subscription.type'].sudo()
        return request.render('modryn_platform.boutique_register', {
            'shops': [s._row() for s in self._shops()],
            'types': Type.search([]),
            'error': error,
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

        # Up to four partners, posted as parallel lists. getlist, because a
        # repeated field name is how an HTML form says "several of these" — the
        # idiom modryn_staff/manage.py already uses for the role-page matrix.
        form = request.httprequest.form
        names = form.getlist('partner_name')
        phones = form.getlist('partner_phone')
        partners = [(0, 0, {'name': n.strip(), 'phone': (phones[i] if i < len(phones) else '').strip()})
                    for i, n in enumerate(names) if n.strip()][:MAX_PARTNERS]
        if partners:
            values['partner_ids'] = partners

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
        for field in ('name', 'city', 'street', 'slug'):
            if field in post:
                values[field] = (post.get(field) or '').strip()
        if post.get('subscription_type_id'):
            values['subscription_type_id'] = int(post['subscription_type_id'])
        elif 'subscription_type_id' in post:
            values['subscription_type_id'] = False
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

    # ------------------------------------------------------ plans and features
    def _plans_context(self, error=None):
        Type = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False)
        Feature = request.env['modryn.platform.feature'].sudo().with_context(
            active_test=False)
        return {
            'types': Type.search([]),
            'features': Feature.search([]),
            'error': error,
        }

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
                })
        except (ValidationError, IntegrityError):
            return request.redirect(
                '/platform/plans?error=%s' % _("That subscription type already exists"))
        return request.redirect('/platform/plans')

    @http.route('/platform/plans/type/features', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def plan_features(self, **post):
        """Which features a tier includes.

        Replace-set from the ticked boxes, never a per-feature toggle: the form
        posts the whole row, so what is stored is exactly what he is looking at.
        A toggle would need the page to already agree with the database, and two
        tabs open on this screen would then disagree with each other.
        """
        if not self._is_platform_owner():
            return request.not_found()
        tier = request.env['modryn.subscription.type'].sudo().with_context(
            active_test=False).browse(int(post.get('type_id') or 0)).exists()
        if not tier:
            return request.redirect('/platform/plans')
        ids = [int(f) for f in request.httprequest.form.getlist('feature_ids') if f.isdigit()]
        tier.write({'feature_ids': [(6, 0, ids)]})
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
