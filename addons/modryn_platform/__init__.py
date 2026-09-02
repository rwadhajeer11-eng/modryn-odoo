from . import models
from . import controllers


def post_init_hook(env):
    """Hebrew first, with Arabic and English beside it.

    THE SAME THREE the boutiques get, and for the same reason: the platform
    owner reads Arabic, his customers read Hebrew, and English is what the
    source strings are written in. Without this the site keeps Odoo's default -
    English only - and the language buttons in the header have nothing to
    switch to.

    Done in a hook and not in a data file because it is not a record to create:
    it is a write onto the website row Odoo made for itself, and `he_IL` has to
    be INSTALLED before it can be pointed at.
    """
    Lang = env['res.lang']
    codes = ('he_IL', 'ar_001', 'en_US')
    for code in codes:
        if not Lang.with_context(active_test=False).search([('code', '=', code)]).active:
            Lang._activate_lang(code)
    langs = Lang.search([('code', 'in', codes)])
    he = langs.filtered(lambda l: l.code == 'he_IL')

    site = env['website'].search([], limit=1)
    if site and he:
        site.write({
            'default_lang_id': he.id,
            'language_ids': [(6, 0, langs.ids)],
        })
