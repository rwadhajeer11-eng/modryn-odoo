from odoo import http
from odoo.http import request

from odoo.addons.website_sale.controllers.main import WebsiteSale


class ModrynShopKinds(WebsiteSale):
    """Let a bride narrow the collection by the KIND of thing she wants.

    Odoo's shop filters on product ATTRIBUTES - size, colour - and on price.
    Neither answers the first question a bride actually asks, which is "show me
    the evening dresses" or "I only want veils". That question is answered by
    modryn.dress.type, the list the boutique writes for itself under
    שמלות ואקססוריז - סוגים, so the filter is HER OWN VOCABULARY: one shop
    filters by נסיכה and מרמייד, the next by עדינה and צנועה, and neither of
    them was given the words by us.

    ARCHIVED KINDS ARE STILL SHOWN if a live dress carries one. A boutique that
    retires a category does not want it offered on new dresses, but a bride
    looking at a dress of that kind should still be able to find its siblings.
    """

    def _modryn_kind_ids(self):
        """The kinds she has ticked, as ints.

        getlist and not the routing kwargs: a repeated query parameter is how a
        checkbox list says "several of these", and Odoo hands the route only
        the last one. The digit check keeps a hand-made ?kind=banana out of
        int(), where it would answer with a 500 instead of an empty filter.
        """
        return [int(v) for v in request.httprequest.args.getlist('kind')
                if str(v).isdigit()]

    @staticmethod
    def _get_attribute_value_dict(attribute_values):
        """Read the size boxes as the form now posts them.

        WHAT BROKE AND WHY. Odoo's size checkboxes are named `attribute_value`
        and mean nothing to the controller on their own: its own JavaScript
        collected them on every change, GROUPED them by attribute into
        `attribute_values=<attribute>-<v1>,<v2>`, and navigated. Taking the
        auto-submit away - so that a bride can tick three things and press once
        - took the grouping with it, and the sizes silently stopped filtering
        while still looking ticked.

        The grouping is arithmetic, not interaction, so it belongs here. The
        plain names are merged into whatever the parent found, which keeps a
        link somebody bookmarked in the old `attribute_values` shape working.
        """
        grouped = WebsiteSale._get_attribute_value_dict(attribute_values)
        for raw in request.httprequest.args.getlist('attribute_value'):
            # "<attribute id>-<value id>", the shape the checkbox carries.
            attribute, _, value = (raw or '').partition('-')
            if not attribute.isdigit() or not value.isdigit():
                continue
            values = grouped.setdefault(int(attribute), [])
            if int(value) not in values:
                values.append(int(value))
        return grouped

    def _shop_lookup_products(self, options, post, search, website):
        """Narrow what the search found to the kinds she asked for.

        FILTERED HERE and not in the search domain, because Odoo's shop search
        goes through the fuzzy-search engine rather than a plain domain, and
        this method is the one place that hands back the whole recordset before
        the page is sliced out of it. Filtering after the slice would give her
        a page of four dresses and a pager promising nine.
        """
        term, count, results = super()._shop_lookup_products(
            options, post, search, website)
        kinds = self._modryn_kind_ids()
        if kinds:
            results = results.filtered(lambda p: p.modryn_type_id.id in kinds)
            count = len(results)
        return term, count, results

    def _shop_get_query_url_kwargs(self, search, min_price, max_price,
                                   order=None, tags=None, **kwargs):
        """Keep her choice across paging, sorting and the price slider.

        Every one of those rebuilds the URL from this dict, and a filter that
        falls off when she turns the page is a filter she stops trusting.
        """
        values = super()._shop_get_query_url_kwargs(
            search, min_price, max_price, order=order, tags=tags, **kwargs)
        values['kind'] = request.httprequest.args.getlist('kind')
        # The sizes as the form posts them, so paging and sorting keep them:
        # the parent carries `attribute_values` out of the session, which the
        # plain checkboxes never fill.
        values['attribute_value'] = request.httprequest.args.getlist('attribute_value')
        return values

    def _get_additional_shop_values(self, values, **kwargs):
        """The list to draw, and which of it is ticked."""
        res = super()._get_additional_shop_values(values, **kwargs)
        Kind = request.env['modryn.dress.type'].sudo()
        # active_test=False, then filtered: a retired kind is offered only when
        # something published still carries it, so the list never grows old
        # names nobody can reach anything with.
        used = request.env['product.template'].sudo().search(
            [('is_published', '=', True), ('modryn_type_id', '!=', False)]
        ).mapped('modryn_type_id')
        res['modryn_kinds'] = Kind.with_context(active_test=False).search(
            [('id', 'in', used.ids)])
        res['modryn_kind_ids'] = self._modryn_kind_ids()
        return res
