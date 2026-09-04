from odoo import _, fields, http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access

_lt = LazyTranslate(__name__)

# Anyone who serves a customer can take her money. The till is not a manager's
# screen: on a Saturday the person standing with the bride is whoever is free,
# and a shop where only two people can complete a sale queues at the counter.
# The owner's controls live on HER screen, not on this one.
nav.register('sell', '/sell', _lt("Sell"), 8, icon='fa-shopping-bag')


class ModrynSell(http.Controller):
    """The till.

    Everything a bridal sale actually is, on one screen: who bought it, what
    went in the bag, what it came to, what was taken off and why, and who
    altered it. Before this the answer to all of that was a price typed into a
    field on an appointment.

    ONE FORM AND ONE SAVE, rather than a draft that fills up line by line. A
    sale is finished at a counter with somebody waiting; a half-written one
    sitting in the database is a thing to find and clean up later, and two
    people sharing a till would find each other's.
    """

    # The catalogue, flattened for the search box. Read fresh on each GET: the
    # rail changes as dresses sell, and a cached list sells one twice.
    def _rail(self):
        Template = request.env['product.template']
        has_serial = 'modryn_serial' in Template._fields
        has_kind = 'modryn_type_id' in Template._fields
        has_accessory = 'modryn_is_accessory' in Template._fields
        rows = []
        for variant in request.env['product.product'].sudo().search(
                [('product_tmpl_id.is_published', '=', True)]):
            tmpl = variant.product_tmpl_id
            size = variant.product_template_attribute_value_ids[:1].name
            rows.append({
                'id': variant.id,
                'label': '%s%s' % (tmpl.name or '',
                                   ' · %s' % size if size else ''),
                'name': tmpl.name or '',
                'serial': (tmpl.modryn_serial or '') if has_serial else '',
                'kind': (tmpl.modryn_type_id.name or '') if has_kind else '',
                # Odoo's own field. A scanner types it and presses Enter, which
                # is why the box treats an exact barcode match as a choice
                # rather than as one more thing to click.
                'barcode': variant.barcode or '',
                'price': round(variant.list_price or 0.0),
                'accessory': bool(tmpl.modryn_is_accessory) if has_accessory
                             else False,
                'stock': variant.modryn_stock
                         if 'modryn_stock' in variant._fields else 0,
            })
        return rows

    def _me(self):
        user = request.env.user
        if user._is_public():
            return None
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)

    def _context(self, error=None, values=None):
        # THE LIVE CODES, and only the ones that would actually work today.
        # A spent code left in the list is a code she picks and is then refused
        # for, which is the box's old uselessness wearing a dropdown.
        codes = request.env['modryn.discount.code'].sudo().search([])
        return {
            'discount_codes': [{
                'code': rule.code,
                # Rendered by the model, so the counter reads "₪200" or "10%"
                # in the same words the manager's own screen uses.
                'takes_off': rule.modryn_takes_off(),
            } for rule in codes if not rule.modryn_spent()],
            'rail': self._rail(),
            'staff': request.env['hr.employee'].sudo().search(
                [], order='name'),
            'error': error,
            # What she typed, handed back so a refusal never costs her the
            # form: a counter is the worst place to retype a bride's number.
            'values': values or {},
            'active_tab': 'sell',
        }

    @http.route('/sell', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def sell_form(self, saved=None, mode=None, rented=None, **kw):
        if not access.can_view('sell'):
            return request.not_found()
        context = self._context()
        # Which of the two the counter is on. Validated against the two that
        # exist rather than passed through: an unknown mode would render a page
        # with neither form on it and no way to tell why.
        context['mode'] = 'rent' if mode == 'rent' else 'sell'
        context['rented'] = request.env['modryn.rental'].sudo().browse(
            int(rented)) if rented and str(rented).isdigit() else None
        if context['rented'] and not context['rented'].exists():
            context['rented'] = None
        context['saved'] = request.env['modryn.sale'].sudo().browse(
            int(saved)) if saved and saved.isdigit() else None
        if context['saved'] and not context['saved'].exists():
            context['saved'] = None
        # Built whole, with the numbers inside it. Split across the markup it
        # exported as the fragments "item(s) ·" and "off", which a translator
        # cannot order - in Hebrew and Arabic the count, the currency and the
        # word all move.
        if context['saved']:
            sale = context['saved']
            paid = '₪{:,}'.format(int(round(sale.total)))
            if sale.discount_amount:
                context['saved_line'] = _(
                    "%(count)s item(s) · %(paid)s · %(discount)s off"
                ) % {'count': len(sale.line_ids), 'paid': paid,
                     'discount': sale.modryn_discount_sentence()}
            else:
                context['saved_line'] = _("%(count)s item(s) · %(paid)s") % {
                    'count': len(sale.line_ids), 'paid': paid}
        return request.render('modryn_ops.sell_screen', context)

    @http.route('/sell/rent', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def rent_out(self, **post):
        """A gown goes out, and the shop starts counting.

        THE WEDDING DATE IS REQUIRED and the collection date is not, because
        the wedding is what lateness is measured from — a rental without one
        can never be late, which is the one thing this whole feature exists to
        notice. Taken-on defaults to now, since that is what pressing the
        button means.
        """
        if not access.can_view('sell'):
            return request.not_found()
        me = self._me()
        if not me:
            return request.not_found()

        def refuse(message):
            context = self._context(error=message, values=dict(post))
            context['mode'] = 'rent'
            context['rented'] = None
            return request.render('modryn_ops.sell_screen', context)

        name = (post.get('customer_name') or '').strip()
        if not name:
            return refuse(_("Please enter the customer's name."))
        wedding = (post.get('wedding_date') or '').strip()
        if not wedding:
            return refuse(_("Say when the wedding is."))

        # THE GOWN, from the rail or typed. The rail answer carries the kind
        # and the ticket price with it, so picking one fills in two boxes the
        # counter would otherwise have to look up while somebody waits.
        variant = None
        raw = (post.get('variant_id') or '').strip()
        if raw.isdigit():
            variant = request.env['product.product'].sudo().browse(
                int(raw)).exists()
        label = (post.get('dress_label') or '').strip()
        kind = (post.get('dress_kind') or '').strip()
        if variant:
            size = variant.product_template_attribute_value_ids[:1].name
            label = label or '%s%s' % (
                variant.product_tmpl_id.name or '',
                ' · %s' % size if size else '')
            if not kind and 'modryn_type_id' in variant.product_tmpl_id._fields:
                kind = variant.product_tmpl_id.modryn_type_id.name or ''
        if not label:
            return refuse(_("Say which gown went out."))

        def money(key, fallback=0.0):
            try:
                return max(float(post.get(key) or 0), 0.0)
            except (TypeError, ValueError):
                return fallback

        retail = money('retail_price')
        if not retail and variant:
            retail = round(variant.list_price or 0.0)

        taken = (post.get('taken_at') or '').strip()
        rental = request.env['modryn.rental'].sudo().create({
            'customer_name': name,
            'customer_phone': (post.get('customer_phone') or '').strip(),
            'variant_id': variant.id if variant else False,
            'dress_label': label,
            'dress_kind': kind,
            'retail_price': retail,
            'rental_price': money('rental_price'),
            'employee_id': me.id,
            'taken_at': ('%s 12:00:00' % taken) if taken else fields.Datetime.now(),
            'wedding_date': wedding,
            'note': (post.get('note') or '').strip(),
        })
        return request.redirect('/sell?mode=rent&rented=%s' % rental.id)

    @http.route('/sell', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def sell_submit(self, **post):
        if not access.can_view('sell'):
            return request.not_found()
        me = self._me()
        if not me:
            return request.not_found()

        name = (post.get('customer_name') or '').strip()
        phone = (post.get('customer_phone') or '').strip()

        # The lines arrive as three parallel lists, one entry per row the
        # screen built. Read together and zipped, because a row is only a row
        # when it has all three.
        variants = request.httprequest.form.getlist('line_variant')
        labels = request.httprequest.form.getlist('line_label')
        prices = request.httprequest.form.getlist('line_price')
        lines = []
        for index, label in enumerate(labels):
            label = (label or '').strip()
            if not label:
                continue
            try:
                price = float((prices[index] if index < len(prices) else '0')
                              or 0)
            except (TypeError, ValueError):
                price = 0.0
            raw = variants[index] if index < len(variants) else ''
            lines.append({
                'variant_id': int(raw) if (raw or '').isdigit() else False,
                'description': label,
                # Never negative. A line that gives money back is a refund, and
                # a refund is a different act on a different screen.
                'price': max(price, 0.0),
            })

        kind = post.get('discount_kind') or 'none'
        if kind not in ('none', 'percent', 'amount'):
            kind = 'none'
        try:
            discount = float(post.get('discount_value') or 0)
        except (TypeError, ValueError):
            discount = 0.0
        reason = (post.get('discount_reason') or '').strip()

        # A CODE OVERRULES THE TWO BOXES BESIDE IT. She typed a word the
        # manager decided in advance; letting a number typed by hand beat it
        # would make the code a suggestion, and there would be no point having
        # one. The reason writes itself, so the owner's screen reads
        # "10% off — BRIDE10" rather than a blank somebody has to chase.
        typed_code = (post.get('discount_code') or '').strip()
        rule = request.env['modryn.discount.code'].sudo().modryn_find(typed_code) \
            if typed_code else None

        altered = bool(post.get('altered'))
        alteration_note = (post.get('alteration_note') or '').strip()
        altered_by_raw = post.get('alteration_by_id') or ''

        def refuse(message):
            return request.render('modryn_ops.sell_screen', self._context(
                error=message, values=dict(post, lines=lines)))

        # A WRONG CODE IS REFUSED, never quietly ignored: a saleswoman who
        # typed BRDIE10 and saw the sale go through would believe the bride got
        # her ten percent, and nobody would find out until the month is counted.
        if typed_code and not rule:
            return refuse(_("There is no discount code called %s.") % typed_code)
        # A SPENT CODE IS REFUSED THE SAME WAY, and told apart from a code that
        # never existed. She typed it correctly and it is finished, which is a
        # different conversation to have with the bride standing there.
        if rule:
            spent = rule.modryn_spent()
            if spent:
                return refuse(spent)
        if rule:
            # THE CODE DECIDES BOTH, kind and number. Hard-coding 'percent'
            # here was right while a code could only be a percentage, and would
            # now quietly turn a two-hundred-shekel code into two hundred
            # percent — a sale that hands money back.
            kind = rule.value_kind
            discount = rule.percent if kind == 'percent' else rule.amount
            reason = rule.code

        if not name:
            return refuse(_("Please enter the customer's name."))
        if not lines:
            return refuse(_("Add at least one thing she is buying."))
        # A discount with no reason is the row the owner would have to chase
        # somebody about, so the form asks for it here rather than letting the
        # tracking screen show a blank.
        if kind != 'none' and discount > 0 and not reason:
            return refuse(_("Say why the discount was given."))
        if kind != 'none' and discount <= 0:
            return refuse(_("Enter how much comes off."))
        if altered and not alteration_note:
            return refuse(_("Say what was altered."))

        sale = request.env['modryn.sale'].sudo().create({
            'customer_name': name,
            'customer_phone': phone,
            'employee_id': me.id,
            'altered': altered,
            'alteration_note': alteration_note if altered else False,
            'alteration_by_id': int(altered_by_raw)
                                if altered and altered_by_raw.isdigit()
                                else False,
            'discount_kind': kind,
            'discount_value': discount if kind != 'none' else 0.0,
            'discount_reason': reason if kind != 'none' else False,
            'line_ids': [(0, 0, line) for line in lines],
        })
        sale.modryn_take_stock()
        if rule:
            # Counted so the manager can see which of her codes anybody is
            # actually using. sudo: the woman at the counter may read a code
            # and may not write one.
            rule.sudo().times_used += 1

        # A discount is money, so it goes in the trail the owner reads. Logged
        # here and not in the model's create, because this is the only door a
        # discount comes through and the audit row wants the ACTOR — env.user,
        # which sudo() preserves and a cron would not have.
        if sale.discount_amount and 'modryn.audit.log' in request.env:
            request.env['modryn.audit.log'].modryn_log(
                record=sale,
                label="Discount",
                field='discount_amount',
                old='{:,}'.format(int(round(sale.subtotal))),
                new='{:,}'.format(int(round(sale.total))),
            )
        return request.redirect('/sell?saved=%s' % sale.id)
