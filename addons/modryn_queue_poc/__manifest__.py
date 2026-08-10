{
    'name': 'MODRYN Queue (realtime probe)',
    'summary': 'QR walk-in check-in and a live queue board driven by bus.bus.',
    'description': """
A deliberately small probe, not a product. Its only job is to price what the
PRD's realtime surfaces (walk-in queue, floor board, SOS paging) would cost in
Odoo, versus MODRYN's shipped decision to poll every 5 seconds.

What it does:
  /queue/checkin   public form a walk-in reaches by scanning a QR code
  Queue Board      backend OWL screen that updates over the websocket, no refresh

The QR image itself is Odoo's built-in /report/barcode endpoint — zero custom
code, which is part of the finding.
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
        # modryn_portal owns the SMS sender port. Depending on it is safe — portal
    # reaches website/booking/theme and never the queue, so there is no cycle —
    # and it is what lets a walk-in be texted when she is next and when it is
    # her turn.
    'depends': ['website', 'bus', 'modryn_theme', 'modryn_portal'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/templates.xml',
        'views/queue_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'modryn_queue_poc/static/src/queue_board/**/*',
        ],
    },
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
