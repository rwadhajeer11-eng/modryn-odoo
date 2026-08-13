from odoo.addons.modryn_staff.models.role_page import DEFAULT_PAGE_KEYS


def migrate(cr, version):
    """Roles that predate the access matrix get the defaults create() now seeds.

    Only roles with NO rows at all — decided ONCE, before any insert, because
    the first key's insert would otherwise make every later key's NOT EXISTS
    false. An owner who has already configured a role by the time this runs
    (re-run, partial upgrade) must not have defaults stuffed back in next to
    her choices.
    """
    cr.execute("""
        SELECT id FROM modryn_staff_role r
         WHERE NOT EXISTS (SELECT 1 FROM modryn_role_page p
                            WHERE p.role_id = r.id)
    """)
    role_ids = [row[0] for row in cr.fetchall()]
    for role_id in role_ids:
        for key in DEFAULT_PAGE_KEYS:
            cr.execute("""
                INSERT INTO modryn_role_page
                       (role_id, page_key, create_uid, write_uid,
                        create_date, write_date)
                VALUES (%s, %s, 1, 1,
                        timezone('utc', now()), timezone('utc', now()))
            """, (role_id, key))
