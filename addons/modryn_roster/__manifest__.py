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
    # Bumped to 19.0.1.1.0 WITH migrations/19.0.1.1.0/ created in the same commit.
    # The two move together or not at all: a bumped version with no matching
    # migrations/<version>/ directory records the new number against every
    # database, so a migration added at that version LATER is skipped forever on
    # exactly the tenants that already have data. Same trap as modryn_booking's.
    #
    # What 19.0.1.1.0 carried: shift_type arrives with default='morning', which
    # would stamp every evening shift a boutique already runs as a morning.
    #
    # What 19.0.1.2.0 carries: modryn.availability is re-keyed off the shift
    # slot and onto (day, shift_type, employee), so a person can offer Friday
    # evening before the boutique has invented a Friday evening shift. Its
    # migration is a PRE-migrate, because the new columns must be filled before
    # _auto_init ever sees them empty - see the file for why a post-migrate here
    # would print green and silently orphan every tick.
    'version': '19.0.1.3.0',
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
    'post_init_hook': 'post_init_hook',
    'author': 'MODRYN',
    'license': 'LGPL-3',
}
