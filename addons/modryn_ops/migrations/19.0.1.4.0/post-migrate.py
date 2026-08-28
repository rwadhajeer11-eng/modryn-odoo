"""Two repairs the catalogue needs, both of which have to run on every tenant.

Deliberately NOT written as one function with early returns. The first draft
returned as soon as it found no negative stock, which meant the serial carry
below it never ran on three of the four databases - the exact shape of bug this
project keeps finding: a step that is skipped silently on precisely the tenants
that looked healthy.
"""

import logging

_logger = logging.getLogger(__name__)


def _has_column(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _repair_negative_stock(cr):
    """Bring any count below zero back to zero, and say how many there were.

    A negative count is a state modryn_stock's own @api.constrains forbids, and
    one exists in the wild anyway: bella carried שמלת כלה אמילי size 36 at -1.
    It predates the constraint, which only validates what is written THROUGH the
    ORM after it existed - so the row sat there failing a rule nothing re-checks.

    What it cost while it sat there: modryn_in_stock sums the sizes, so the whole
    dress read as one fewer than the boutique had, and modryn_sold_out is
    `total <= 0` - a dress with a real dress on the rail in another size could
    show as out of stock to the owner and, now that the shop reads the same flag,
    to a bride.

    Clamped to zero rather than guessed: nobody knows how many were really there,
    and zero is the only number that is certainly not an overstatement.
    """
    if not _has_column(cr, 'product_product', 'modryn_stock'):
        return
    cr.execute("SELECT count(*) FROM product_product WHERE modryn_stock < 0")
    bad = cr.fetchone()[0]
    if not bad:
        _logger.info("modryn_ops: no negative stock to repair")
        return
    cr.execute("UPDATE product_product SET modryn_stock = 0 WHERE modryn_stock < 0")
    _logger.info("modryn_ops: repaired %d variant(s) that held negative stock", bad)


def _carry_serials(cr):
    """Move any serial off Odoo's field and onto the dress itself.

    modryn_serial used to be related='default_code'. On a template with more than
    one variant that field is not writable at all - it is a non-stored compute
    over the variants - so every serial an owner typed for a dress with sizes was
    accepted by the form and silently discarded. Measured on a fresh dress with
    three sizes: serial typed, saved, default_code came back False.

    Where a single-variant product DID keep one, that is a real serial somebody
    entered and it has to survive the change.
    """
    if not _has_column(cr, 'product_template', 'modryn_serial'):
        return
    cr.execute("""
        UPDATE product_template t
           SET modryn_serial = v.default_code
          FROM product_product v
         WHERE v.product_tmpl_id = t.id
           AND t.modryn_serial IS NULL
           AND v.default_code IS NOT NULL
           AND v.default_code <> ''
    """)
    if cr.rowcount:
        _logger.info("modryn_ops: carried %d serial(s) onto the dress itself",
                     cr.rowcount)
    else:
        _logger.info("modryn_ops: no serials to carry")


def migrate(cr, version):
    _repair_negative_stock(cr)
    _carry_serials(cr)
