from odoo import _, http
from odoo.http import request

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers.manage import ModrynManage

nav.register('dresses', '/manage/dresses', _("Dresses"), 15, 'manage', 'fa-diamond')


class ModrynDresses(ModrynManage):
    """The rail, as the owner keeps it.

    Extends the owner's manage controller by inheritance - the same seam the
    roster and atelier use - so this page inherits _require_owner and the
    /manage section rules without restating them. /manage/* is owner-only by
    deliberate decision and never enters the role matrix.
    """

    def _dress_rows(self):
        Product = request.env['product.template'].sudo()
        rows = []
        for dress in Product.search([('type', '=', 'consu')], order='name'):
            rows.append({
                'id': dress.id,
                'name': dress.name,
                'price': dress.list_price,
                'serial': dress.default_code or '',
                'published': dress.is_published,
                'in_stock': dress.modryn_in_stock,
                'sold_out': dress.modryn_sold_out,
                'sizes': [{
                    'id': v.id,
                    # The size, read off the variant's own attribute values -
                    # not a string parsed out of its display name, which is
                    # translated and would stop matching in Arabic.
                    'label': ' / '.join(
                        v.product_template_variant_value_ids.mapped(
                            'product_attribute_value_id.name')) or _("One size"),
                    'stock': v.modryn_stock,
                } for v in dress.product_variant_ids],
            })
        return rows

    @http.route('/manage/dresses', type='http', auth='user', website=True,
                sitemap=False)
    def dresses(self, error=None, **kw):
        if not self._require_owner():
            return request.not_found()
        return request.render('modryn_ops.manage_dresses', {
            'dresses': self._dress_rows(),
            'error': error,
            'active_tab': 'dresses',
        })

    @http.route('/manage/dresses/stock', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dresses_stock(self, **post):
        """Set how many of one size are on the rail.

        Replace-set from a typed number, not a +1/-1 button: the owner counts
        the rail and types what she sees. Two people incrementing from two
        phones would each be counting from a different reading.
        """
        if not self._require_owner():
            return request.not_found()
        variant = request.env['product.product'].sudo().browse(
            int(post.get('variant_id') or 0)).exists()
        if not variant:
            return request.redirect('/manage/dresses')
        try:
            count = int(post.get('stock') or 0)
        except ValueError:
            return request.redirect(
                '/manage/dresses?error=%s' % _("Please enter a whole number"))
        if count < 0:
            return request.redirect(
                '/manage/dresses?error=%s' % _("Stock cannot go below zero."))
        variant.modryn_stock = count
        return request.redirect('/manage/dresses')

    @http.route('/manage/dresses/serial', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dresses_serial(self, **post):
        if not self._require_owner():
            return request.not_found()
        dress = request.env['product.template'].sudo().browse(
            int(post.get('dress_id') or 0)).exists()
        if dress:
            dress.default_code = (post.get('serial') or '').strip() or False
        return request.redirect('/manage/dresses')
