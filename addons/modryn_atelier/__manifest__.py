{
    'name': 'MODRYN Atelier',
    'summary': 'Alteration tasks for seamstresses: which garment piece, for whom, by when.',
    'description': """
A bridal gown is almost never sold as it hangs — it is altered. This is the workshop side
of the boutique:

  * garment pieces (hem, bodice, sleeves, train, straps) the OWNER maintains as data
  * an alteration task per job: customer, dress and size, pieces, note, seamstress, due date
  * intake -> in progress -> ready -> delivered
  * /atelier   manager and owner: every task by state, load per seamstress, overdue flagged
  * on /floor  each seamstress sees HER OWN tasks and advances them herself

The seamstress advancing her own work is what keeps the dashboard truthful — the same
reason floor occupancy is derived rather than typed.
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
    'depends': ['modryn_staff', 'modryn_booking'],
    'data': [
        'security/ir.model.access.csv',
        'data/garment_piece_data.xml',
        'views/atelier_templates.xml',
    ],
    'author': 'MODRYN',
    'assets': {
        # The workshop's own search box. web.assets_frontend, because this is a
        # website page and not the back office - and a bundle here is DEFERRED,
        # which is why atelier.js is an Interaction and not a DOMContentLoaded
        # listener: that event has already fired by the time the bundle runs.
        'web.assets_frontend': [
            'modryn_atelier/static/src/atelier.js',
        ],
    },
    'license': 'LGPL-3',
}
