{
    'name': 'MODRYN Platform',
    'summary': "The platform owner's register of subscribing boutiques.",
    'description': """
Which boutiques subscribe, and on what.

This module answers a question no boutique database can: MODRYN runs many
boutiques, each isolated in its own PostgreSQL database by construction, and
nothing anywhere lists them. Provision a boutique and it exists; the only record
that it does is a row in `pg_database` and a line in someone's memory.

What lives here is PLATFORM data — the shop's number, its address, who its
partners are, which subscription it is on. None of it is a boutique's own data,
so none of it needs reading out of a boutique's database, and this module is
deliberately NOT installed into modryn_template: a boutique owner must never
open a screen that lists her competitors.

It is a register, not a dashboard. Live per-shop figures (today's bookings, this
month's fittings) would mean reading every tenant database on one page, which is
a different and much larger thing — see .planning/BACKLOG.md.
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
    'depends': [
        'website',
    ],
    'data': [
        'security/platform_groups.xml',
        'security/ir.model.access.csv',
        'data/subscription_type_data.xml',
        'views/platform_templates.xml',
        'views/login_templates.xml',
    ],
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
