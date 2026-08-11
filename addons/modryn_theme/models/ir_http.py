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
                if not isinstance(value, models.BaseModel):
                    continue
                requested = value.env.context.get('_converter_value')
                if not requested or cls._unslug(requested)[0] is None:
                    # No name half at all (/shop/2). Still Odoo's to canonicalise.
                    continue
                # with_env escapes the converter's RequestUID env. sudo() keeps an
                # unreadable record from turning this into a 500 — super() 404s it
                # a moment later either way, so it is not an oracle. The lang must
                # be the URL's: display_name, and so the slug, is translatable,
                # and request.env is not langed until super() runs.
                record = value.with_env(request.env).sudo().with_context(lang=request.lang.code)
                # cls._slug, never a re-slugify of display_name: website overrides
                # _slug to prefer seo_name, and a record carrying an SEO name
                # would otherwise 404 on its own canonical URL.
                if record.exists() and cls._slug(record) != requested:
                    raise werkzeug.exceptions.NotFound()
        super()._pre_dispatch(rule, args)
