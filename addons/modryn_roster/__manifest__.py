{
    'name': 'MODRYN Roster',
    'summary': 'Owner-defined shifts, staff availability, and next week\'s roster.',
    'description': """
Who is working next Tuesday, and is that enough people?

The shifts themselves are the boutique's own data — a boutique that opens late
on Thursdays and runs a Saturday-night bridal evening must be able to say so
without a developer. Staff tick the shifts they can work from the same terminal
they already use; the manager fills the grid and publishes it.

Coverage targets are per shift AND per role, because two saleswomen and no
seamstress is not the same as one of each, even though both are "two people".
""",
    'category': 'Website',
    'version': '19.0.1.0.0',
    'depends': [
        'modryn_staff',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/shift_template_data.xml',
        'views/roster_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'modryn_roster/static/src/**/*',
        ],
    },
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
