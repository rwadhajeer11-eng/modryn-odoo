#!/usr/bin/env python3
"""Every string the product shows, checked against what the .po files carry.

WHY THIS EXISTS, and why the existing gate check is not enough.

verify.sh already asserts that every entry IN a .po file is filled in. It says
so in its own comment: it cannot see a string that was never added to the file
at all. That is exactly what a brand-new screen produces - a whole screen once
shipped in English while the gate stayed green.

And there is a second, quieter gap that even a msgid comparison misses. A
`model_terms` translation binds to the VIEW named in its reference lines, not to
the word. So a string already translated for one screen renders in ENGLISH on
the next screen that uses it, until the .po entry names that view too. Measured:
"Bride" was translated, a msgid-only sweep said nothing was missing, and the
supervisor's page printed "Bride".

So this asks the two questions that catch both:

  1. Is every source string present and translated in every language we ship?
  2. Does every translation reach every VIEW the source now uses it in?

It needs a database to export the .pot from, because the source of truth for
"what strings exist" is what Odoo itself extracts, not a grep of the templates.
That makes it slower than a file check and worth every second of it - measured
at four seconds for the whole product.

Usage:
    python3 scripts/i18n_audit.py [--db qa] [--langs he,ar]

Exit codes, matching verify.sh's convention:
    0  clean
    1  something is untranslated or unbound
    2  could not run (no polib, no database, export failed) - report as SKIP,
       never as a pass. A check that cannot run has not passed.
"""

import argparse
import glob
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def modules():
    """Every addon in this repo that ships translations."""
    return [os.path.basename(os.path.dirname(p))
            for p in sorted(glob.glob(os.path.join(REPO, 'addons', '*', 'i18n')))]


def export_pot(db, mods, out):
    """Ask Odoo what strings exist.

    One call for all modules rather than one per module: measured at four
    seconds against twenty-something, which is the difference between a check
    that runs in the gate and one that gets skipped.
    """
    odoo = os.path.join(REPO, 'odoo', 'odoo-bin')
    conf = os.path.join(REPO, 'odoo.conf')
    if not os.path.exists(odoo) or not os.path.exists(conf):
        return False
    cmd = [sys.executable, odoo, 'i18n', 'export',
           '-c', conf, '-d', db, '-l', 'pot', '-o', out] + mods
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def module_of_reference(ref, known):
    """Which addon a single reference line belongs to.

    Read off the REFERENCE and not off the entry's `#. module:` comment, which
    was the first version of this and was wrong in a way that hid work: in a
    combined export an entry used by two modules is merged into one, and its
    comment becomes `#. modules: a, b` - plural. Matching the singular form
    silently skipped every shared string, 115 of them, and a check that skips
    that quietly is worse than no check.

    Two shapes carry the module name:
        code:addons/<module>/static/src/...
        <anything>:<model>,<field>:<module>.<xmlid>
    """
    if ref.startswith('code:addons/'):
        # "code:addons/modryn_staff/static/..." splits to
        # ['code:addons', 'modryn_staff', 'static', ...] - the module is at 1,
        # not 2. Getting that wrong left every code reference unattributed, and
        # the only reason it was noticed is that the script counts what it
        # skips: 330 skipped, and 330 code references in the file.
        parts = ref.split('/')
        if len(parts) > 1 and parts[1] in known:
            return parts[1]
        return None
    tail = ref.rsplit(':', 1)[-1]
    head = tail.split('.', 1)[0]
    return head if head in known else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('MODRYN_I18N_DB', 'qa'))
    parser.add_argument('--langs', default='he,ar')
    args = parser.parse_args()
    langs = [lang.strip() for lang in args.langs.split(',') if lang.strip()]

    try:
        import polib
    except ImportError:
        print('SKIP: polib is not installed')
        return 2

    mods = modules()
    if not mods:
        print('SKIP: no addons with an i18n directory')
        return 2
    known = set(mods)

    handle, pot_path = tempfile.mkstemp(suffix='.pot')
    os.close(handle)
    try:
        if not export_pot(args.db, mods, pot_path):
            print('SKIP: could not export the .pot from database %r' % args.db)
            return 2

        # (module, msgid) -> the references that belong to THAT module. A string
        # shared by two addons is two entries here, each carrying only its own
        # uses, which is exactly the shape each module's own .po has.
        source = {}
        unattributed = 0
        for entry in polib.pofile(pot_path):
            for ref in entry.occurrences:
                mod = module_of_reference(ref[0], known)
                if mod is None:
                    unattributed += 1
                    continue
                source.setdefault((mod, entry.msgid), set()).add(ref)

        problems = []
        for mod in mods:
            wanted = {msgid: occ for (m, msgid), occ in source.items() if m == mod}
            if not wanted:
                continue
            for lang in langs:
                path = os.path.join(REPO, 'addons', mod, 'i18n', '%s.po' % lang)
                if not os.path.exists(path):
                    problems.append('%s/%s.po is missing (%d strings)'
                                    % (mod, lang, len(wanted)))
                    continue
                have = {e.msgid: set(e.occurrences)
                        for e in polib.pofile(path) if e.msgstr.strip()}
                for msgid in sorted(set(wanted) - set(have)):
                    problems.append('%s %s: untranslated %r'
                                    % (mod, lang, msgid.replace('\n', ' ')[:70]))
                for msgid, occ in sorted(wanted.items()):
                    if msgid not in have:
                        continue
                    # Only view bindings matter. A `code:` reference is a
                    # runtime lookup by string, so it translates wherever it is
                    # called from; a model_terms one is stored against the view
                    # it names and reaches nowhere else.
                    short = {o for o in occ
                             if o[0].startswith('model_terms:')} - have[msgid]
                    if short:
                        problems.append(
                            '%s %s: %r is translated but not bound to %s'
                            % (mod, lang, msgid.replace('\n', ' ')[:50],
                               ', '.join(sorted(o[0] for o in short))))
    finally:
        try:
            os.unlink(pot_path)
        except OSError:
            pass

    if unattributed:
        # Core Odoo strings reached through an inherited view, and the like.
        # Reported rather than hidden: a number that climbs is this script
        # quietly stopping to look at things.
        print('(%d references belong to no addon of ours - not checked)'
              % unattributed)
    if problems:
        for line in problems[:40]:
            print(line)
        if len(problems) > 40:
            print('... and %d more' % (len(problems) - 40))
        return 1
    print('every source string is translated, and every translation is bound')
    return 0


if __name__ == '__main__':
    sys.exit(main())
