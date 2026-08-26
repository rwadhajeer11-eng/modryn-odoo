"""Move availability off the shift slot and onto (day, part-of-day).

Runs in PRE-migrate, and that is not a style choice. Odoo's order on -u is
pre-migrate -> _auto_init -> post-migrate. If week_start/day/shift_type first
appeared during _auto_init they would arrive NULL on every existing row;
required=True then makes Odoo attempt SET NOT NULL, which fails inside a
savepoint and only WARNS; and the unique index builds fine afterwards because
Postgres treats NULLs as distinct. The upgrade would print green, the grid would
draw twenty-one empty cells, and every tick anybody had ever made would be
orphaned in a column nothing reads. So the columns are added AND FILLED here,
before the ORM gets a chance to see them empty.

What it does, in order:
  1. add the three new columns, nullable for now
  2. fill them by joining through the slot to its template
  3. delete rows whose slot_id resolves to nothing (only possible if the FK was
     already broken) - loudly, at ERROR level
  4. merge rows that collapse onto one cell (two templates sharing a weekday and
     a part of the day) - the new key is narrower than the old one, so this is
     a real possibility rather than a defensive nicety
  5. release slot_id: DROP NOT NULL and drop the old unique constraint

Step 5 is the one that would bite silently if it were forgotten. Odoo does not
drop the column of a field you removed, and slot_id is NOT NULL today - so
every future insert, from a worker simply tapping a cell, would fail on a column
no code mentions any more.

The column itself is deliberately KEPT. Dropping it destroys the only evidence
of what this migration did, and a nullable orphan column costs nothing.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'modryn_availability' AND column_name = 'slot_id'
    """)
    if not cr.fetchone():
        # Already re-keyed, or a database that never had the old shape.
        _logger.info("modryn_roster: availability has no slot_id, nothing to move")
        return

    cr.execute("SELECT count(*) FROM modryn_availability")
    before = cr.fetchone()[0]

    cr.execute("""
        ALTER TABLE modryn_availability
            ADD COLUMN IF NOT EXISTS week_start date,
            ADD COLUMN IF NOT EXISTS day        date,
            ADD COLUMN IF NOT EXISTS shift_type varchar
    """)

    # The join is total by construction: slot_id is NOT NULL and carries a
    # foreign key, and modryn_shift_slot.template_id is likewise required. The
    # COALESCE mirrors the model's own default for a template that predates the
    # shift_type field.
    cr.execute("""
        UPDATE modryn_availability a
           SET day        = s.day,
               week_start = s.week_start,
               shift_type = COALESCE(t.shift_type, 'morning')
          FROM modryn_shift_slot s
     LEFT JOIN modryn_shift_template t ON t.id = s.template_id
         WHERE a.slot_id = s.id
           AND a.day IS NULL
    """)
    moved = cr.rowcount

    # Anything still unfilled had a slot_id pointing at nothing, which the
    # foreign key should have made impossible. Say so at ERROR level rather than
    # carrying a row that can never satisfy required=True.
    cr.execute("DELETE FROM modryn_availability WHERE day IS NULL")
    orphaned = cr.rowcount
    if orphaned:
        _logger.error(
            "modryn_roster: dropped %d availability row(s) whose shift no longer "
            "existed - their foreign key was already broken", orphaned)

    # The new key (day, shift_type, employee) is NARROWER than the old
    # (slot, employee): a boutique running two templates on the same weekday at
    # the same part of the day had two rows that are now one cell. Keep the
    # earliest and count the rest - "I can work Sunday morning" said twice is
    # still said once.
    cr.execute("""
        DELETE FROM modryn_availability a
              USING modryn_availability b
              WHERE a.day = b.day
                AND a.shift_type = b.shift_type
                AND a.employee_id = b.employee_id
                AND a.id > b.id
    """)
    merged = cr.rowcount
    if merged:
        _logger.info(
            "modryn_roster: merged %d duplicate offer(s) - the same person had "
            "offered two shifts that share one day and part of the day", merged)

    # Release the old column so inserts that never mention it can succeed.
    cr.execute("ALTER TABLE modryn_availability ALTER COLUMN slot_id DROP NOT NULL")
    cr.execute("ALTER TABLE modryn_availability "
               "DROP CONSTRAINT IF EXISTS modryn_availability_slot_employee_uniq")

    if before and not moved:
        # A silent zero from a backfill that had rows to move is the one failure
        # nothing downstream can report: the upgrade succeeds, the table looks
        # populated, and every cell on the grid is empty.
        raise AssertionError(
            "modryn_roster: %d availability row(s) existed but none were moved - "
            "refusing to finish an upgrade that would silently empty the grid"
            % before)

    _logger.info("modryn_roster: re-keyed %d/%d availability row(s) onto "
                 "(day, shift_type, employee)", moved, before)
