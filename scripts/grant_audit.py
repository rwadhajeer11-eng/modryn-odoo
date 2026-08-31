#!/usr/bin/env python3
"""Every column in the access matrix must be a tick that can deliver.

THE DEFECT THIS EXISTS FOR, found twice on this machine:

    /manage/dresses was offered in the owner's access matrix as an ordinary
    column while its own route called _require_owner. Measured: granted Dresses
    to a role, the tab appeared in that woman's navbar, and pressing it gave
    404. /manage/pieces had exactly the same shape and nobody had noticed -
    because a bottom-row page is only reachable after a grant somebody has to
    make first, so no test and no person had ever walked that path.

    That second one is why this is a script and not a behavioural check.
    verify.sh's navbar sweep asks "does every tab this manager is SHOWN open?",
    which can only cover pages she can already see. A page waiting behind a
    grant nobody has made is invisible to it.

Reads SOURCE, not a running Odoo: the nav registry is built by nav.register()
calls scattered across the controllers, and every one of them is a line in a
file. That also means it runs in under a second with no database, so there is no
excuse not to run it while working.

    0  every offered page asks the matrix
    1  a column would hand somebody a 404
    2  could not run (say so rather than passing silently)

    python3 scripts/grant_audit.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = os.path.join(ROOT, 'addons')
NAV = os.path.join(ADDONS, 'modryn_staff', 'nav.py')

# Gates that mean "ask the matrix". can_view answers True for the owner before
# it looks at anything else, so a page using it is still hers - it has simply
# stopped being ONLY hers.
MATRIX_GATES = ('access.can_view(', 'access.is_manager()', 'self._is_manager()')

# Gates that mean "the owner, and nobody a tick can add".
OWNER_GATES = ('_require_owner()', 'has_group(GROUP_OWNER)',
               "has_group('modryn_staff.group_boutique_owner')")

# Pages nothing grants and nothing should: 'home' and 'profile' are never
# columns because no matrix state may strand a signed-in woman.
ALWAYS_OPEN = ('home', 'profile')


def python_files():
    for base, _dirs, files in os.walk(ADDONS):
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(base, name)


def read(path):
    try:
        return open(path, encoding='utf-8').read()
    except OSError:
        return ''


def tuple_from_nav(name):
    """A tuple constant out of nav.py, without importing Odoo to get it."""
    m = re.search(r"^%s\s*=\s*\(([^)]*)\)" % name, read(NAV), re.M)
    if m is None:
        return None
    return tuple(re.findall(r"'([a-z_]+)'", m.group(1)))


def registered_pages():
    """key -> url, off every nav.register call in the tree."""
    pages = {}
    for path in python_files():
        for m in re.finditer(r"register\(\s*'([a-z_]+)'\s*,\s*'([^']+)'",
                             read(path)):
            pages.setdefault(m.group(1), m.group(2))
    return pages


def route_gate(url):
    """The gate on the route serving `url`: 'owner', 'matrix', None, 'missing'."""
    # Anchored on the closing quote so /manage/dresses does not match
    # /manage/dresses/new, and stopping at the next route so a neighbour's gate
    # is never read as this one's.
    pattern = (r"@http\.route\(\s*'%s'[^)]*\)\s*\n((?:.|\n)*?)"
               r"(?=\n    @http\.route|\Z)" % re.escape(url))
    for path in python_files():
        m = re.search(pattern, read(path))
        if not m:
            continue
        body = m.group(1)[:1500]
        if any(g in body for g in OWNER_GATES):
            return 'owner', path
        if any(g in body for g in MATRIX_GATES):
            return 'matrix', path
        return None, path
    return 'missing', None


def main():
    if not os.path.exists(NAV):
        print('cannot find %s' % NAV, file=sys.stderr)
        return 2

    never = tuple_from_nav('NEVER_GRANTABLE')
    owner_only = tuple_from_nav('OWNER_ONLY')
    if never is None or owner_only is None:
        print('nav.py no longer declares NEVER_GRANTABLE and OWNER_ONLY as '
              'plain tuples - this audit can no longer read them, which is a '
              'failure and not a pass', file=sys.stderr)
        return 2

    pages = registered_pages()
    if len(pages) < 8:
        print('found only %d registered pages - this audit is not reading the '
              'registry it thinks it is' % len(pages), file=sys.stderr)
        return 2

    offered = {k: u for k, u in pages.items()
               if k not in never and k not in owner_only and k not in ALWAYS_OPEN}

    problems = []
    for key in sorted(offered):
        gate, path = route_gate(offered[key])
        where = os.path.relpath(path, ROOT) if path else '?'
        if gate == 'owner':
            problems.append(
                "%s (%s) is a column in the access matrix, but its route in %s "
                "refuses anybody but the owner. A tick there hands the woman a "
                "tab that answers 404. Either let that route ask the matrix, or "
                "name '%s' in nav.OWNER_ONLY so the matrix draws it locked."
                % (key, offered[key], where, key))
        elif gate == 'missing':
            problems.append(
                "%s (%s) is a column in the access matrix and no route serves "
                "that address at all." % (key, offered[key]))

    if problems:
        for line in problems:
            print(line)
        return 1
    print('every page the access matrix offers is one a tick can actually open '
          '(%d checked)' % len(offered))
    return 0


if __name__ == '__main__':
    sys.exit(main())
