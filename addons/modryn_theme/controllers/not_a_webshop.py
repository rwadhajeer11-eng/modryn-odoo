from odoo import http
from odoo.http import request

# Where a bride is sent instead. Not the collection: she arrived at a checkout
# because she wanted THAT dress, and the booking form is the thing this product
# has instead of a "buy" button.
INSTEAD = '/book'


class ModrynNotAWebshop(http.Controller):
    """A bridal boutique is not a webshop, and now the routes agree.

    modryn.scss has hidden the "add to cart" chrome since the theme was
    written, and says in its own comment that display-level removal is not the
    fix. This is the rest of it. What was actually reachable, signed out, on a
    dress costing eleven thousand shekels:

        Add to cart · Add to compare · Add to wishlist · a cart in the header ·
        /shop/cart · and from there Order › Address › Payment

    None of it can complete - no payment provider is configured - so what it
    offered a customer was a checkout that fails at the last step, which is
    worse than no checkout at all. The views stop it being reachable; these
    routes stop a typed or bookmarked URL walking in behind them.

    A REDIRECT AND NOT A 404, because a 404 says "this shop is broken" and the
    truth is "this shop takes appointments". Anyone who got here wanted a
    dress; the booking form is where that goes.

    NOTHING IS UNINSTALLED. website_sale is what serves /shop itself - the
    collection, the product pages, the sizes - so it stays, and only the buying
    half of it is closed.
    """

    @http.route(['/shop/cart',
                 '/shop/checkout',
                 '/shop/confirm_order',
                 '/shop/extra_info',
                 '/shop/address',
                 '/shop/payment',
                 '/shop/confirmation'],
                type='http', auth='public', website=True, sitemap=False)
    def modryn_no_checkout(self, **kw):
        """No args declared beyond **kw: these routes take different ones each,
        and naming any of them here would 500 on the ones that pass another."""
        return request.redirect(INSTEAD)
