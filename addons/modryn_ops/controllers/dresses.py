import base64
import io
import re

import werkzeug.urls
from PIL import Image

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from odoo.addons.modryn_staff import nav
from odoo.addons.modryn_staff.controllers import access
from odoo.addons.modryn_staff.controllers.manage import ModrynManage

_lt = LazyTranslate(__name__)

# A dress photo straight off a phone is 3-6 MB, and she will add several. The
# ceiling is per FILE and is enforced in the handler rather than by nginx, so
# the refusal is a sentence on her own page instead of a 413 from a server she
# has never heard of.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# The attribute the boutique keeps its sizes in. One per database, found by
# name and created on first use - not seeded, because a boutique that only
# sells accessories never needs one and an empty attribute in the product form
# is a question with no answer.
SIZE_ATTRIBUTE = "מידה"

# _lt, not _. This line runs at IMPORT time, when there is no request and no
# language to detect, so _() resolves once against nothing and freezes English
# into the registry for every user forever - the Dresses tab read "Dresses" on a
# Hebrew page and an Arabic one while its .po entry sat there translated. _lt is
# the lazy form that resolves when the label is finally rendered, which is what
# every other nav.register in the product already uses.
# The TOP row: the rail is daily work, not back-office administration. Which
# means every shift MANAGER opens it without anybody granting anything - that is
# what the top row is - and any role the owner ticks Dresses for opens it too.
# The route below asks the matrix, so the row, the tick and the gate all say the
# same thing. They did not always: this page sat in the matrix as an ordinary
# column while its route refused everybody but the owner, and a tick handed the
# woman a tab that answered 404.
nav.register('dresses', '/manage/dresses', _lt("Dresses"), 30, 'staff', 'fa-diamond')


class ModrynDresses(ModrynManage):
    """The rail, as the boutique keeps it.

    Extends the owner's manage controller by inheritance - the same seam the
    roster and atelier use - for its helpers, NOT for its gate. Every route here
    asks access.can_view('dresses'): the owner always, every shift manager
    because this page sits in the top row, and any role the owner has ticked.

    The whole page moves together, writes included. A catalogue somebody may
    read with an Add button that answers 404 is the same lie as a tab that does
    - and everywhere else in this matrix a tick means "this role may use this
    page", not "may look at it".
    """

    def _dress_rows(self):
        # active_test=False, so an archived dress is still listed and can be
        # brought back. A row that vanishes when it is archived leaves the owner
        # no way to undo, and the only remaining route is the Odoo back office.
        Product = request.env['product.template'].sudo().with_context(
            active_test=False)
        rows = []
        for dress in Product.search([('type', '=', 'consu')],
                                    order='active desc, name'):
            rows.append({
                'id': dress.id,
                'name': dress.name,
                'price': dress.list_price,
                'serial': dress.modryn_serial or '',
                'published': dress.is_published,
                'in_stock': dress.modryn_in_stock,
                'sold_out': dress.modryn_sold_out,
                'active': dress.active,
                'kind': dress.modryn_type_id.name or '',
                'kind_id': dress.modryn_type_id.id,
                'is_accessory': dress.modryn_is_accessory,
                'description': dress.description_sale or '',
                # Every photo she has added, the main one first. image_1920 is
                # Odoo's own field and product_template_image_ids is its own
                # extra-images model - no second store of our own, so the shop
                # and this page can never show different pictures.
                'images': ([{'id': 0, 'main': True}] if dress.image_1920 else []) + [
                    {'id': img.id, 'main': False}
                    for img in dress.product_template_image_ids],
                'sizes': [{
                    'id': v.id,
                    # The size, read off the variant's own attribute values -
                    # not a string parsed out of its display name, which is
                    # translated and would stop matching in Arabic.
                    'label': ' / '.join(
                        v.product_template_variant_value_ids.mapped(
                            'product_attribute_value_id.name')) or _("One size"),
                    'stock': v.modryn_stock,
                    # Said per size, because "we have none of THIS one" is a
                    # different sentence from "we have none at all" and the
                    # bride in front of you is asking the first one.
                    'sold_out': v.modryn_stock <= 0,
                } for v in dress.product_variant_ids],
            })
        return rows

    @http.route('/manage/dresses', type='http', auth='user', website=True,
                sitemap=False)
    def dresses(self, error=None, **kw):
        if not access.can_view('dresses'):
            return request.not_found()
        return request.render('modryn_ops.manage_dresses', {
            'dresses': self._dress_rows(),
            'error': error,
            'active_tab': 'dresses',
            # THE LINK SHE GIVES OUT. Built from the host this request came in
            # on, never written down: every boutique is its own subdomain and
            # the same code serves all of them, so a hardcoded address would be
            # one shop's address printed on every shop's screen. It also
            # follows the machine - localtest.me here, the real domain in
            # production - with nothing to remember to change.
            #
            # No language prefix: a bride should land in the shop's own
            # language, not in whichever one the manager happened to be
            # reading her own screen in.
            'shop_url': request.httprequest.url_root.rstrip('/') + '/shop',
        })

    # ------------------------------------------------------------- helpers
    def _back(self, post):
        """/manage/dresses, landing on the row she was working on.

        Every form here used to redirect to the bare page, so the browser went
        to the top and she scrolled back down to the dress she had just touched
        - on a rail of thirty, that is the whole page every time she changes a
        number.

        The fragment is validated rather than reflected: it is echoed into a
        Location header, and anything the page did not put there has no business
        being in one.
        """
        anchor = (post.get('back') or '').strip()
        if anchor and re.fullmatch(r'dress-\d+', anchor):
            return '/manage/dresses#%s' % anchor
        return '/manage/dresses'

    def _kinds(self):
        return request.env['modryn.dress.type'].sudo().search([])

    def _size_attribute(self):
        """The boutique's size attribute, made once and then reused.

        Found by NAME rather than by an XML id, because these databases are
        clones of a template that already carries one, and a second attribute
        called the same thing would split every dress in the shop into two
        unrelated size lists that no screen would ever show together.
        """
        Attr = request.env['product.attribute'].sudo()
        attr = Attr.search([('name', '=', SIZE_ATTRIBUTE)], limit=1)
        if not attr:
            attr = Attr.create({
                'name': SIZE_ATTRIBUTE,
                # 'always', so every size she types becomes a real variant she
                # can count. 'dynamic' only makes one when something is ordered,
                # and nothing in this product ever orders anything.
                'create_variant': 'always',
            })
        return attr

    def _sizes_from(self, post):
        """The sizes she typed, in her order, with duplicates dropped.

        A free text box and not a fixed list: 34/36/38 is one boutique's range,
        XS/S/M is another's, and a veil that comes in one size has to be able to
        say so by leaving the box empty.
        """
        raw = (post.get('sizes') or '').replace(chr(10), ',').replace(';', ',')
        out = []
        for piece in raw.split(','):
            label = piece.strip()
            if label and label not in out:
                out.append(label)
        return out

    def _apply_sizes(self, dress, sizes):
        """Make the dress have exactly these sizes and no others.

        Odoo does the variant bookkeeping itself once attribute_line_ids is
        right; writing product.product rows by hand here would leave orphans it
        later cannot explain. Removing a size therefore deletes the variant that
        held its count, which is why the form says so before she saves.
        """
        Value = request.env['product.attribute.value'].sudo()
        if not sizes:
            dress.attribute_line_ids.unlink()
            return
        attr = self._size_attribute()
        value_ids = []
        for label in sizes:
            value = Value.search(
                [('attribute_id', '=', attr.id), ('name', '=', label)], limit=1)
            if not value:
                value = Value.create({'attribute_id': attr.id, 'name': label})
            value_ids.append(value.id)
        line = dress.attribute_line_ids.filtered(
            lambda l: l.attribute_id == attr)
        if line:
            line.value_ids = [(6, 0, value_ids)]
        else:
            dress.attribute_line_ids = [(0, 0, {
                'attribute_id': attr.id, 'value_ids': [(6, 0, value_ids)]})]

    def _read_image(self, storage, errors):
        """One uploaded photo as base64, or None with the reason recorded.

        Measured HERE rather than left to the web server: a limit enforced by
        nginx answers with a 413 page in English from a host the owner has never
        heard of, and she has no way to tell that from the site being broken.
        """
        if not storage or not storage.filename:
            return None
        blob = storage.read()
        if not blob:
            return None
        if len(blob) > MAX_IMAGE_BYTES:
            errors['images'] = _(
                "%(name)s is too big - photos have to be under %(mb)s MB.",
                name=storage.filename, mb=MAX_IMAGE_BYTES // (1024 * 1024))
            return None
        # Opened HERE, so a file that is not a picture is a sentence on her form
        # instead of an Internal Server Error. Odoo hands the bytes to Pillow
        # inside create(), and Pillow raises from deep in the ORM where nothing
        # catches it - a heic straight off an iPhone, a pdf picked by mistake, or
        # a half-finished upload all took the whole page down with a 500 and
        # nothing on screen to say which file was the problem.
        #
        # verify() and not just open(): open() reads the header only, so a
        # truncated photo passes it and fails later in exactly the same place.
        try:
            Image.open(io.BytesIO(blob)).verify()
        except Exception:
            errors['images'] = _(
                "%(name)s is not a picture we can read - try a JPG or a PNG.",
                name=storage.filename)
            return None
        return base64.b64encode(blob)

    def _dress_values(self, post, errors):
        """The typed fields, validated. Photos and sizes are handled apart."""
        name = (post.get('name') or '').strip()
        if not name:
            errors['name'] = _("Please give it a name.")
        price_raw = (post.get('price') or '').strip().replace(',', '.')
        price = 0.0
        if price_raw:
            try:
                price = float(price_raw)
            except ValueError:
                errors['price'] = _("The price has to be a number.")
        if price < 0:
            errors['price'] = _("The price cannot be less than zero.")
        kind_id = post.get('kind_id')
        kind = None
        if kind_id and str(kind_id).isdigit():
            # Checked against the kinds that exist, so a hand-made POST cannot
            # attach an id from another tenant's numbering.
            kind = self._kinds().filtered(lambda k: k.id == int(kind_id))
        return {
            'name': name,
            'list_price': price,
            'modryn_serial': (post.get('serial') or '').strip() or False,
            'description_sale': (post.get('description') or '').strip() or False,
            'modryn_type_id': kind.id if kind else False,
            'is_published': bool(post.get('published')),
        }

    def _dress_form(self, dress=None, values=None, errors=None):
        return request.render('modryn_ops.manage_dress_form', {
            'dress': dress,
            'kinds': self._kinds(),
            'values': values or {},
            'errors': errors or {},
            'max_mb': MAX_IMAGE_BYTES // (1024 * 1024),
            'active_tab': 'dresses',
        })

    # ------------------------------------------------------ add and change
    @http.route('/manage/dresses/new', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def dress_new_form(self, **kw):
        if not access.can_view('dresses'):
            return request.not_found()
        return self._dress_form(values={'published': True})

    @http.route('/manage/dresses/new', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dress_new_submit(self, **post):
        if not access.can_view('dresses'):
            return request.not_found()
        errors = {}
        vals = self._dress_values(post, errors)
        files = request.httprequest.files.getlist('images')
        images = [img for img in (self._read_image(f, errors) for f in files) if img]
        if errors:
            # Her typing comes back, photos apart - a browser will not let a
            # page re-fill a file input, so the form says so rather than
            # pretending the pictures survived.
            return self._dress_form(values=dict(
                post, published=bool(post.get('published'))), errors=errors)
        dress = request.env['product.template'].sudo().create(dict(
            vals,
            # 'consu' is what the catalogue has always searched on: a thing on a
            # rail, not a service.
            type='consu',
            image_1920=images[0] if images else False,
        ))
        self._apply_sizes(dress, self._sizes_from(post))
        for extra in images[1:]:
            request.env['product.image'].sudo().create({
                'name': dress.name, 'image_1920': extra,
                'product_tmpl_id': dress.id})
        return request.redirect('/manage/dresses')

    @http.route('/manage/dresses/edit/<int:dress_id>', type='http', auth='user',
                website=True, methods=['GET'], sitemap=False)
    def dress_edit_form(self, dress_id, **kw):
        if not access.can_view('dresses'):
            return request.not_found()
        dress = request.env['product.template'].sudo().with_context(
            active_test=False).browse(dress_id).exists()
        if not dress:
            return request.not_found()
        return self._dress_form(dress=dress, values={
            'name': dress.name,
            # Trimmed, so 8900.00 reads as 8900 in the box she types into.
            'price': ('%.2f' % dress.list_price).rstrip('0').rstrip('.'),
            'serial': dress.modryn_serial or '',
            'description': dress.description_sale or '',
            'kind_id': dress.modryn_type_id.id,
            'published': dress.is_published,
            'sizes': ', '.join(
                v.name for v in dress.attribute_line_ids.filtered(
                    lambda l: l.attribute_id.name == SIZE_ATTRIBUTE).value_ids),
        })

    @http.route('/manage/dresses/edit/<int:dress_id>', type='http', auth='user',
                website=True, methods=['POST'], csrf=True, sitemap=False)
    def dress_edit_submit(self, dress_id, **post):
        if not access.can_view('dresses'):
            return request.not_found()
        dress = request.env['product.template'].sudo().with_context(
            active_test=False).browse(dress_id).exists()
        if not dress:
            return request.not_found()
        errors = {}
        vals = self._dress_values(post, errors)
        files = request.httprequest.files.getlist('images')
        images = [img for img in (self._read_image(f, errors) for f in files) if img]
        if errors:
            return self._dress_form(dress=dress, values=dict(
                post, published=bool(post.get('published'))), errors=errors)
        # A new main photo ONLY when one was actually sent. An empty file input
        # is how every browser says "she did not change the picture", and
        # writing False for that would silently delete the photo she had.
        if images:
            vals['image_1920'] = images[0]
        dress.write(vals)
        self._apply_sizes(dress, self._sizes_from(post))
        for extra in images[1:]:
            request.env['product.image'].sudo().create({
                'name': dress.name, 'image_1920': extra,
                'product_tmpl_id': dress.id})
        return request.redirect('/manage/dresses')

    @http.route('/manage/dresses/photo/<int:image_id>/delete', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def dress_photo_delete(self, image_id, **post):
        if not access.can_view('dresses'):
            return request.not_found()
        image = request.env['product.image'].sudo().browse(image_id).exists()
        if image:
            image.unlink()
        return request.redirect('/manage/dresses')

    @http.route('/manage/dresses/archive/<int:dress_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def dress_archive(self, dress_id, **post):
        """Off the shop, without losing what it was.

        Archived and never deleted: past appointments point at this dress and at
        the size that was sold, and deleting the product turns those into blank
        rows nobody can explain a year later. Unpublishing on the way out too,
        so an archived dress cannot sit on the shop with nothing listing it.
        """
        if not access.can_view('dresses'):
            return request.not_found()
        dress = request.env['product.template'].sudo().with_context(
            active_test=False).browse(dress_id).exists()
        if dress:
            going_away = dress.active
            dress.write({
                'active': not going_away,
                'is_published': False if going_away else dress.is_published,
            })
        return request.redirect('/manage/dresses')

    # ------------------------------------------------- the boutique's kinds
    def _kind_rows(self):
        Product = request.env['product.template'].sudo().with_context(
            active_test=False)
        rows = []
        for kind in request.env['modryn.dress.type'].sudo().with_context(
                active_test=False).search([]):
            rows.append({
                'id': kind.id,
                'name': kind.name,
                'is_accessory': kind.is_accessory,
                'active': kind.active,
                # Shown so she can see what archiving one would orphan, before
                # she does it rather than afterwards.
                'used': Product.search_count([('modryn_type_id', '=', kind.id)]),
            })
        return rows

    @http.route('/manage/dress-kinds', type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def dress_kinds(self, error=None, **kw):
        if not access.can_view('dresses'):
            return request.not_found()
        return request.render('modryn_ops.manage_dress_kinds', {
            'kinds': self._kind_rows(),
            'error': error,
            'active_tab': 'dresses',
        })

    @http.route('/manage/dress-kinds', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dress_kind_add(self, **post):
        if not access.can_view('dresses'):
            return request.not_found()
        name = (post.get('name') or '').strip()
        if not name:
            return request.redirect(
                '/manage/dress-kinds?error=%s' % _("Please type a name."))
        try:
            request.env['modryn.dress.type'].sudo().create({
                'name': name,
                'is_accessory': bool(post.get('is_accessory')),
            })
        except ValidationError as exc:
            # A duplicate is a normal thing to type twice, not a 500. The
            # model's own sentence is the one she reads.
            return request.redirect(
                '/manage/dress-kinds?error=%s' % werkzeug.urls.url_quote(
                    exc.args[0] if exc.args else _("That kind already exists.")))
        return request.redirect('/manage/dress-kinds')

    @http.route('/manage/dress-kinds/archive/<int:kind_id>', type='http',
                auth='user', website=True, methods=['POST'], csrf=True,
                sitemap=False)
    def dress_kind_archive(self, kind_id, **post):
        """Retire a kind without breaking what already points at it.

        Archived, never deleted: items keep their kind and their history stays
        readable. An archived kind simply stops being offered on the form.
        """
        if not access.can_view('dresses'):
            return request.not_found()
        kind = request.env['modryn.dress.type'].sudo().with_context(
            active_test=False).browse(kind_id).exists()
        if kind:
            kind.active = not kind.active
        return request.redirect('/manage/dress-kinds')

    @http.route('/manage/dresses/stock', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dresses_stock(self, **post):
        """Set how many of one size are on the rail.

        Replace-set from a typed number, not a +1/-1 button: the owner counts
        the rail and types what she sees. Two people incrementing from two
        phones would each be counting from a different reading.
        """
        if not access.can_view('dresses'):
            return request.not_found()
        variant = request.env['product.product'].sudo().browse(
            int(post.get('variant_id') or 0)).exists()
        if not variant:
            return request.redirect('/manage/dresses')
        try:
            count = int(post.get('stock') or 0)
        except ValueError:
            return request.redirect('%s?error=%s' % (
                self._back(post), _("Please enter a whole number")))
        if count < 0:
            return request.redirect('%s?error=%s' % (
                self._back(post), _("Stock cannot go below zero.")))
        variant.modryn_stock = count
        return request.redirect(self._back(post))

    @http.route('/manage/dresses/serial', type='http', auth='user', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def dresses_serial(self, **post):
        if not access.can_view('dresses'):
            return request.not_found()
        dress = request.env['product.template'].sudo().browse(
            int(post.get('dress_id') or 0)).exists()
        if dress:
            dress.modryn_serial = (post.get('serial') or '').strip() or False
        return request.redirect(self._back(post))
