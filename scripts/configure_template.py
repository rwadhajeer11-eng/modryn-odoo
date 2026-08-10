# Runs inside `odoo-bin shell` against modryn_template.
# Everything set here is inherited by every boutique cloned from the template,
# so keep it to platform-wide defaults only — no tenant content.

# --- Languages -------------------------------------------------------------
# he_IL was loaded by --load-language at init. Arabic is installed here so the
# storefront language selector has a second option (PRD: Hebrew default,
# Arabic toggle). Both are RTL; Hebrew's week starts Sunday natively.
Lang = env['res.lang'].with_context(active_test=False)
he = Lang.search([('code', '=', 'he_IL')], limit=1)
ar = Lang.search([('code', '=', 'ar_001')], limit=1)
# en_US is the SOURCE language: our addon strings are written in English and
# he/ar arrive as .po translations. It must be active for the storefront
# switcher to offer it, even though Hebrew stays the default.
en = Lang.search([('code', '=', 'en_US')], limit=1)
assert he and ar and en, 'expected he_IL, ar_001 and en_US in res.lang'

to_install = (he | ar | en).filtered(lambda lang: not lang.active)
if to_install:
    # The wizard both flips `active` and loads each installed module's .po terms.
    env['base.language.install'].create({
        'lang_ids': [(6, 0, to_install.ids)],
        'overwrite': False,
    }).lang_install()

# --- Currency --------------------------------------------------------------
ils = env['res.currency'].with_context(active_test=False).search([('name', '=', 'ILS')], limit=1)
ils.active = True
try:
    env.company.currency_id = ils
except Exception as exc:  # noqa: BLE001 - report, don't mask
    print('WARN could not set company currency: %s' % exc)

# --- Product variants ------------------------------------------------------
# The size matrix (34..44 with per-size quantities) is Odoo variants. Without
# this setting the attribute UI stays hidden.
env['res.config.settings'].create({'group_product_variant': True}).execute()

# --- Website ---------------------------------------------------------------
site = env['website'].search([], limit=1)
site.write({
    'default_lang_id': he.id,
    'language_ids': [(6, 0, (he | ar | en).ids)],
})

env.cr.commit()
# Variants are a "group" setting: it is granted by making base.group_user imply
# product.group_product_variant. Checking env.user is wrong here — the shell
# runs as __system__ (uid 1), which is not a member of base.group_user.
variants_on = env.ref('product.group_product_variant') in env.ref('base.group_user').implied_ids
print('TEMPLATE_CONFIGURED langs=%s default=%s currency=%s variants=%s' % (
    site.language_ids.mapped('code'),
    site.default_lang_id.code,
    env.company.currency_id.name,
    variants_on,
))
