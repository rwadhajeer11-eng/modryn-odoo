"""Save every woman's role before the column that holds it disappears.

Odoo's order on -u is pre-migrate -> _auto_init -> post-migrate.
modryn_role_id stops being a stored column at _auto_init, so by post-migrate
time the old values are gone. This step copies them somewhere safe first; the
post-migrate puts them into the new many-to-many.

Two steps rather than one, and deliberately NOT writing the relation table
here: its name is Odoo's to choose, _auto_init has not created it yet, and a
guessed name would leave the real table empty. An empty many-to-many is not an
error - it is just an empty many-to-many - so every employee would silently
lose her role, role_page.modryn_can_view would fall to `if not roles: return
False`, and the whole team would be shut out of every page but her own two,
with nothing in the log to say why.

The post-migrate writes through the ORM instead, which puts the rows wherever
Odoo actually built the table.
"""

import logging

_logger = logging.getLogger(__name__)

CARRY = 'modryn_role_carry_19_1_7'


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'hr_employee' AND column_name = 'modryn_role_id'
    """)
    if not cr.fetchone():
        _logger.info("modryn_staff: no modryn_role_id column, nothing to save")
        return

    cr.execute("DROP TABLE IF EXISTS %s" % CARRY)
    cr.execute("""
        CREATE TABLE %s AS
        SELECT id AS employee_id, modryn_role_id AS role_id
          FROM hr_employee
         WHERE modryn_role_id IS NOT NULL
    """ % CARRY)
    cr.execute("SELECT count(*) FROM %s" % CARRY)
    saved = cr.fetchone()[0]
    _logger.info("modryn_staff: saved %d employee role(s) for the new m2m", saved)
