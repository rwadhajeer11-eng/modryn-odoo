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
    # Not bumped for the floor-board integration: it is new Python and a read
    # helper, with no data to move. A bumped version with no matching
    # migrations/<version>/ directory records the new number against every
    # database, so a migration added at that version LATER would be skipped
    # forever on exactly the tenants that already exist. Same reasoning, and the
    # same trap, as modryn_booking's manifest.
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
