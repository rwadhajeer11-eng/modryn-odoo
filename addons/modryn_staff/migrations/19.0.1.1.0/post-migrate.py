"""Carry existing helper assignments into the new through-model.

The old many2many tables held no join time, so there is nothing better to seed
create_date with than "now" — the ordering only becomes meaningful for helpers
added from here on. Losing that history is acceptable; losing the ASSIGNMENTS
would not be, because the floor board would quietly forget who is helping whom.
"""


def migrate(cr, version):
    for old_table, column in (('modryn_queue_helper_rel', 'entry_id'),
                              ('modryn_event_helper_rel', 'event_id')):
        cr.execute("SELECT to_regclass(%s)", (old_table,))
        if not cr.fetchone()[0]:
            continue
        cr.execute("""
            INSERT INTO modryn_floor_helper (%(column)s, employee_id, create_date, write_date,
                                             create_uid, write_uid)
            SELECT %(column)s, employee_id, now() at time zone 'UTC',
                   now() at time zone 'UTC', 1, 1
            FROM %(old_table)s
            ON CONFLICT DO NOTHING
        """ % {'column': column, 'old_table': old_table})
        # The relation table is now dead weight; keeping it invites a future
        # reader into thinking it is still the source of truth.
        cr.execute("DROP TABLE %s" % old_table)
