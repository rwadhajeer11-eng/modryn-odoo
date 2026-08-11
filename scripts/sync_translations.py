#!/usr/bin/env python3
"""Rewrite our .po files so every msgid is EXACTLY what Odoo extracts.

Why this exists: a QWeb translation unit is the inner HTML of a block, inline
tags included. A <div><span>Price on request</span></div> is extracted as the
term `<span>Price on request</span>`, NOT `Price on request`. A hand-written
msgid without the markup looks perfect, loads without complaint, and silently
never matches — the page just renders English.

So: export the POT with Odoo itself, then re-key our existing translations onto
the exported msgids by comparing their tag-stripped text.

    source .venv/bin/activate
    python3 scripts/sync_translations.py bella

Existing translations are preserved; only the keys change. Anything Odoo extracts
that we have no translation for is reported so it can be filled in.
"""
import html
import os
import re
import subprocess
import sys

import polib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# (addon, languages we ship). Staff screens are Hebrew + English only, by
# product decision — no Arabic.
ADDONS = [
    ('modryn_theme', ['he', 'ar']),
    ('modryn_booking', ['he', 'ar']),
    ('modryn_queue_poc', ['he', 'ar']),
    ('modryn_staff', ['he']),
    ('modryn_portal', ['he', 'ar']),
    ('modryn_atelier', ['he']),
    ('modryn_roster', ['he']),
    # Customer-facing SMS bodies ship he + ar; the staff pages are he-only like
    # every other staff surface, and the ar file simply never gains UI terms.
    ('modryn_ops', ['he', 'ar']),
]

TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'\s+')


def normalize(text):
    """Tag-stripped, whitespace-collapsed text — the thing a human recognises.

    Case is PRESERVED deliberately. Odoo extracts "Available" (the badge on a
    staff card) and "available" (the lowercase suffix in "3 available") as two
    distinct terms with two different Hebrew forms — פנויה singular versus
    פנויות plural. Lowercasing here silently merged them and dropped one, so the
    board rendered English for that word with nothing to explain why.
    """
    return WS_RE.sub(' ', html.unescape(TAG_RE.sub(' ', text or ''))).strip()


def export_pot(db, addon, out):
    subprocess.run(
        [os.path.join(REPO, 'odoo', 'odoo-bin'), 'i18n', 'export',
         '-c', os.path.join(REPO, 'odoo.conf'), '-d', db, '-l', 'pot',
         '-o', out, '--', addon],
        cwd=REPO, check=True, capture_output=True,
    )


def header(lang):
    return {
        'Project-Id-Version': 'Odoo Server 19.0',
        'Report-Msgid-Bugs-To': '',
        'Last-Translator': '',
        'Language-Team': '',
        'Language': lang,
        'MIME-Version': '1.0',
        'Content-Type': 'text/plain; charset=UTF-8',
        'Content-Transfer-Encoding': '8bit',
        'Plural-Forms': '',
    }


def main(db):
    for addon, langs in ADDONS:
        addon_dir = os.path.join(REPO, 'addons', addon)
        if not os.path.isdir(addon_dir):
            continue
        pot_path = '/tmp/%s.pot' % addon
        export_pot(db, addon, pot_path)
        pot = polib.pofile(pot_path)

        for lang in langs:
            po_path = os.path.join(addon_dir, 'i18n', '%s.po' % lang)
            existing = {}
            if os.path.exists(po_path):
                for entry in polib.pofile(po_path):
                    if entry.msgstr:
                        existing[normalize(entry.msgid)] = entry.msgstr

            out = polib.POFile()
            out.metadata = header(lang)
            matched, missing = 0, []
            for entry in pot:
                if not entry.msgid:
                    continue
                key = normalize(entry.msgid)
                translation = existing.get(key)
                if not translation:
                    missing.append(entry.msgid)
                    continue
                new = polib.POEntry(
                    msgid=entry.msgid,
                    msgstr=translation,
                    occurrences=entry.occurrences,
                    comment=entry.comment,
                )
                out.append(new)
                matched += 1

            os.makedirs(os.path.dirname(po_path), exist_ok=True)
            out.save(po_path)
            print('%-18s %s: %d translated, %d untranslated' % (addon, lang, matched, len(missing)))
            for msgid in missing[:5]:
                print('      no translation for: %r' % msgid[:70])


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'bella')
