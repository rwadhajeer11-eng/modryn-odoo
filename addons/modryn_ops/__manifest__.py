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
    'version': '19.0.1.3.0',
    'depends': ['modryn_staff', 'modryn_portal'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/ops_templates.xml',
    ],
    # Wired to BOTH lifecycle paths: cloned tenants INSTALL (hooks), the
    # hand-built ones upgrade (migrations/) — see schema_guard.py.
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
