"""Put the saved roles into the new many-to-many.

Runs after _auto_init, so the relation table now exists under whatever name
Odoo chose for it - which is exactly why this writes through the ORM rather
than INSERTing into a table name guessed in the pre-migrate.

Refuses to finish if rows were saved and none landed. A silent zero is the one
failure nothing downstream can report: the upgrade succeeds, the old column is
gone, and the whole team is role-less with no record of why.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CARRY = 'modryn_role_carry_19_1_7'


def migrate(cr, version):
    cr.execute("SELECT to_regclass('%s')" % CARRY)
    if not cr.fetchone()[0]:
        _logger.info("modryn_staff: nothing was saved, nothing to restore")
        return

    cr.execute("SELECT employee_id, role_id FROM %s" % CARRY)
    saved = cr.fetchall()
    if not saved:
        cr.execute("DROP TABLE %s" % CARRY)
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Employee = env['hr.employee'].sudo().with_context(active_test=False)

    restored = 0
    for employee_id, role_id in saved:
        employee = Employee.browse(employee_id).exists()
        if not employee:
            # Deleted between the two halves of one upgrade - not possible in
            # practice, but a missing row must not abort the rest.
            continue
        employee.modryn_role_ids = [(4, role_id)]
        restored += 1

    if saved and not restored:
        raise AssertionError(
            "modryn_staff: %d role(s) were saved and none were restored - "
            "refusing to finish an upgrade that would strip the team"
            % len(saved))

    cr.execute("DROP TABLE %s" % CARRY)
    _logger.info("modryn_staff: restored %d/%d employee role(s)",
                 restored, len(saved))

    # hr_employee.modryn_role_id, the COLUMN, is deliberately left behind.
    # Odoo never drops a column on upgrade, and dropping this one is the single
    # irreversible step in an otherwise reversible change - on a production
    # boutique that call is not this script's to make.
    #
    # It is inert: modryn_role_id is a non-stored compute now, so nothing in the
    # ORM reads or writes the column, no raw SQL in this repo names it, and its
    # foreign key is ON DELETE SET NULL, so it cannot block deleting a role.
    # What it does hold is every woman's role as it was BEFORE this upgrade -
    # so anything that queries it later gets a confident, plausible, stale
    # answer. Read hr_employee_modryn_staff_role_rel, never this column.
