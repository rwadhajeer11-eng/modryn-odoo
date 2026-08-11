import werkzeug.exceptions

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        # A <model(...)> URL is "<name>-<id>", but the converter browses the id
        # and throws the name away (base/models/ir_http.py, ModelConverter.
        # to_python). http_routing then notices the URL is not the record's
        # canonical one and 301s onto it — nice SEO inside one site, and across
        # two tenants it means bella's dress link, opened on noga, answers with
        # NOGA's dress of the same id. Nothing leaks (the record is noga's own)
        # but a shared link lands the visitor in the wrong boutique. A name that
        # belongs to no record here is a wrong URL, so: 404.
        #
        # BEFORE super() on purpose. That 301 sits at the tail of the inherited
        # _pre_dispatch and aborts, so a check placed after super() never sees
        # the mismatching case at all.
        #
        # Not a ModelConverter subclass, for two reasons that are not style:
        # reading any field inside to_python raises TypeError (its env holds a
        # RequestUID placeholder, not an int uid), and to_python runs before
        # lang detection, so a translated name would false-404.
        if getattr(request, 'is_frontend_multilang', False) \
                and request.httprequest.method in ('GET', 'HEAD'):
            for value in args.values():
                # len check as well as the isinstance: <models(...)> resolves to a
                # multi-record set, and cls._slug unpacks value.id/display_name,
                # which would raise "Expected singleton" — a 500 on a frontend
                # GET. It never carries _converter_value today, so the guard below
                # already skips it; this makes that safety explicit rather than
                # incidental.
                if not isinstance(value, models.BaseModel) or len(value) != 1:
                    continue
                requested = value.env.context.get('_converter_value')
                if not requested or cls._unslug(requested)[0] is None:
                    # No name half at all (/shop/2). Still Odoo's to canonicalise.
                    continue
                # with_env escapes the converter's RequestUID env, in which
                # reading any field raises TypeError. sudo() keeps a record the
                # public user cannot read from turning this into a 500, and it is
                # not an oracle: unreadable-with-a-matching-slug 404s in super()
                # via check_access, unreadable-with-a-wrong-slug 404s here, and
                # the two responses are byte-identical.
                base = value.with_env(request.env).sudo()
                if not base.exists():
                    continue
                # Every language the site publishes, not just this request's.
                # display_name is translatable, so the slug is too — and a real
                # browser sending Accept-Language: en-US is 303'd to /en/ BEFORE
                # this runs, so the boutique's own canonical Hebrew link arrives
                # here to be compared against its English slug. Today the two are
                # the same string and nothing breaks; the day the owner
                # translates one dress name, a single-language comparison would
                # 404 the canonical link for every en-defaulting visitor. The
                # request's own language is tried first, so the common case costs
                # one read.
                langs = request.env['website'].get_current_website().language_ids.mapped('code')
                ordered = [request.lang.code] + [c for c in langs if c != request.lang.code]
                # cls._slug, never a re-slugify of display_name: website overrides
                # _slug to prefer seo_name, and a record carrying an SEO name
                # would otherwise 404 on its own canonical URL.
                if not any(cls._slug(base.with_context(lang=c)) == requested for c in ordered):
                    raise werkzeug.exceptions.NotFound()
        super()._pre_dispatch(rule, args)
