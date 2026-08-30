{
    'name': 'MODRYN Ops',
    'summary': 'Appointment outcomes, follow-up automation and an audit trail.',
    'description': """
A booking used to just... stop. This module gives it an ending, and makes the ending do work:

  * every fitting closes as SOLD, NOT SOLD or NO-SHOW — from the floor board, by a manager
    or by the stylist who ran it
  * each outcome fires its flow: a thank-you text, a feedback text (always, stamped so the
    manager can see it went out), or a warm rebook text
  * managers get a "still to close" nag until every past booking has an outcome — outcomes
    are what conversion numbers are made of, so none may quietly evaporate
  * an append-only audit trail (who, what, old, new) for outcome edits and stylist swaps,
    readable by the owner at /manage/audit

Later features hang follow-up tasks (modryn.task), bride categories and the KPI reports off
the hooks this module defines.
""",
    'category': 'Website',
    # 19.0.1.4.0 ships WITH migrations/19.0.1.4.0/. The catalogue gains a
    # kind, a description and photos, and the repair for stock counts that
    # went below zero before the constraint existed. A version bumped without
    # that directory records the number against every database and the
    # migration is then skipped FOREVER on exactly the tenants holding data.
    #
    # 19.0.1.5.0 deliberately ships WITHOUT one: a walk-in gains the three
    # outcome fields a booking already had, and three new nullable columns are
    # something _auto_init creates by itself. There is nothing to convert -
    # every existing walk-in is correctly "no outcome recorded" - so a
    # migration script would have nothing to do but log that it ran.
    'version': '19.0.1.5.0',
    'depends': ['modryn_staff', 'modryn_portal'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/ops_templates.xml',
        'views/dress_templates.xml',
        'views/shop_templates.xml',
    ],
    # Wired to BOTH lifecycle paths: cloned tenants INSTALL (hooks), the
    # hand-built ones upgrade (migrations/) — see schema_guard.py.
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'assets': {
        # Plain script, guarded by an element only the catalogue renders - the
        # same shape as modryn_staff's home.js, which also rides this bundle.
        'web.assets_frontend': [
            'modryn_ops/static/src/dresses.js',
        ],
    },
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
