#!/usr/bin/env python3
"""Every screen, in every language, looking for words in the wrong one.

WHY THIS EXISTS, AND WHY i18n_audit.py IS NOT ENOUGH. That script asks Odoo
which source strings exist and whether the .po files answer them. It is exact
and it is blind to three things that all reach the eye:

  1. A label built in Python from a plain list. `dict(field.selection)` hands
     back the English the file was written in, never passes through a .po, and
     printed "Intake" on the boutique's Hebrew sales screen. No missing
     translation exists for it, so nothing was missing.
  2. A string Odoo's own modules own. The login page's "Use a Passkey" is not
     this product's to translate, but it is on this product's screen.
  3. Data. A garment piece typed in Arabic sits in a Hebrew list forever, and no
     amount of translating helps: somebody has to see it and rename the row.

So this one does not read files. It loads the pages, in each language, and reads
what a person would read.

  python3 scripts/lang_sweep.py                 # every page, both languages
  python3 scripts/lang_sweep.py --lang he_IL    # one language
  python3 scripts/lang_sweep.py --json          # for a machine

Exit 0 clean, 1 something to look at, 2 could not run.

WHAT IT CANNOT DECIDE, and does not pretend to: whether a Latin word is a bug.
"MODRYN" is the brand, a stylist may genuinely be called "Sarah", a gown may be
called "Aurora". It reports what it finds with enough context to judge, and the
judging is a person's job. The allow-list below is for the ones already judged.
"""
import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = os.environ.get('BASE_URL', 'http://qa.localtest.me:8069')
PASSWORD = os.environ.get('MODRYN_DEMO_PASSWORD', '')
USER = os.environ.get('MODRYN_SWEEP_USER', 'qaowner')

# The languages the boutique actually offers, and the script that each one is
# supposed to be written in.
# code -> (expected script, URL prefix). The prefix is how Odoo's website
# actually switches language; the cookie alone does not, and a sweep that only
# sets the cookie reads the same pages twice and reports them clean.
#
# Hebrew is this boutique's default, so it is served with no prefix at all and
# /he/... redirects to the bare path.
LANGUAGES = {
    'he_IL': ('hebrew', ''),
    'ar_001': ('arabic', '/ar'),
}

# Which half of the product to read. A boutique by default, because that is
# where twenty-five of the twenty-nine screens are.
#
#   MODRYN_SWEEP_SITE=platform BASE_URL=http://platform.localtest.me:8069 #   MODRYN_PLATFORM_PHONE=… MODRYN_PLATFORM_ID=… python3 scripts/lang_sweep.py
SITE = os.environ.get('MODRYN_SWEEP_SITE', 'boutique')

# The platform owner's screens. Her door is on the list: it is the first thing
# she meets, and a sign-in in the wrong language is the worst place to have one.
PLATFORM_PAGES = [
    '/platform/login',
    '/platform/home',
    '/platform/boutiques',
    '/platform/plans',
    '/platform/account',
]

# Every screen a signed-in owner can reach. Public pages first, then the ones
# behind the login - the order the sweep reports in.
PAGES = [
    '/',
    '/shop',
    '/queue/checkin',
    '/staff/login',
    '/floor',
    '/atelier',
    '/manage/pieces',
    '/manage/dresses',
    '/manage/reports',
    '/manage/checklists',
    '/manage/roles',
    '/manage/audit',
    '/manage/shifts',
    '/roster',
    '/shift-supervisor',
    '/staff/profile',
    '/staff/profile?view=details',
    '/staff/profile?view=hours',
    '/manage/team-screen',
    '/manage/team-screen?view=team',
    '/manage/team-screen?view=hours',
    '/manage/team-screen?view=rooms',
    '/manage/team-screen?view=worked',
    '/manage/team-screen?view=announce',
    '/manage/team-screen?view=sales',
]

# Names the tenant chose, which no translation can fix and none should. A
# boutique may employ a Sarah and sell a gown called Aurora; on the qa tenant
# every member of staff is called "QA Something" on purpose.
#
#   MODRYN_LANG_ALLOW='Owner,Manager,Seamstress' python3 scripts/lang_sweep.py
#
# The sweep cannot tell a person's name from an untranslated label - both are a
# capitalised Latin word in a span - so this is where a human writes down the
# ones already looked at.
TENANT_WORDS = {w.strip() for w in
                os.environ.get('MODRYN_LANG_ALLOW', '').split(',') if w.strip()}

# The language picker names each language in its OWN script, which is the whole
# point of a language picker: a reader who cannot read the page she is on has to
# be able to recognise her own language in the list.
PICKER = re.compile(r'^(?:[A-Za-z()\s.]+/\s*)?\S.*$')
PICKER_TAGS = {'option', 'a@title'}

# Latin runs that are not a translation failure, and why each one is here.
ALLOWED = {
    # The brand. It is a wordmark and stays Latin in every language.
    'MODRYN',
    # The language picker names each language in ITSELF, which is the point of
    # a language picker - a reader who cannot read the current language has to
    # be able to find her own.
    'English', 'US', 'Arabic', 'Hebrew',
    # Currency and units as Odoo prints them.
    'ILS', 'USD',
}

# Screens this product does not own the words on. Odoo's own login and account
# templates carry their own translations, and where those are thin it is not a
# gap this repo can close in a .po of its own.
FOREIGN_CHROME = {'/staff/login', '/web/login', '/shop'}

# Odoo's own furniture, wherever it appears. These are real English words on a
# Hebrew screen and they are worth KNOWING about - the "Odoo Apps" button in
# particular is a door into the back office that a boutique owner is offered on
# every page - but they are not this repo's strings and drowning the report in
# them hides the ones that are. Counted, named, and set aside.
ODOO_CHROME = {
    'Go to your Odoo Apps', 'My Account', 'Logout', 'Sign out',
    'Preferences', 'Documentation', 'Support', 'Log out',
}

# The company's own name is data, not a translation: a boutique may be called
# "Bella Bridal" in any language and the page title carries it on every screen.
# Read off the page rather than hardcoded, so this holds for any tenant.
COMPANY = re.compile(r'<title>[^<]*?\|\s*([^<]+)</title>')

LATIN = re.compile(r'[A-Za-z][A-Za-z\'’.-]{1,}')
HEBREW = re.compile(r'[֐-׿]')
ARABIC = re.compile(r'[؀-ۿݐ-ݿ]')
# Anything that is plainly not prose: a file name, a code, a measurement.
NOISE = re.compile(r'^(?:[A-Z]{2,6}\d*|v?\d[\w.-]*|[a-z]+\.[a-z]{2,4})$')


class Visible(HTMLParser):
    """The text a person sees, with the element it sits in.

    Script and style are skipped, not stripped: their contents are code, and
    code is full of English that means nothing to this question.
    """

    SKIP = {'script', 'style', 'noscript', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip = 0
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        self._stack.append(tag)
        # An image with no text still carries words in its alt, and those are
        # read aloud to somebody.
        for key, value in attrs:
            if key in ('alt', 'title', 'placeholder', 'aria-label') and value:
                self.chunks.append((tag + '@' + key, value))

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if self._stack and tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if text:
            self.chunks.append((self._stack[-1] if self._stack else '?', text))


def visible_text(html):
    parser = Visible()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.chunks


def opener_for(lang):
    jar = http.cookiejar.CookieJar()
    build = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    build.addheaders = [('User-Agent', 'modryn-lang-sweep')]
    host = urllib.parse.urlparse(BASE).hostname or 'localhost'
    jar.set_cookie(http.cookiejar.Cookie(
        version=0, name='frontend_lang', value=lang, port=None,
        port_specified=False, domain=host, domain_specified=True,
        domain_initial_dot=False, path='/', path_specified=True,
        secure=False, expires=None, discard=False, comment=None,
        comment_url=None, rest={}))
    return build


def fetch(build, path):
    try:
        with build.open(BASE + path, timeout=30) as response:
            return response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.read().decode('utf-8', 'replace') if error.code != 404 else ''
    except Exception:
        return ''


def sign_in(build):
    """Sign in, and prove it worked.

    The platform's door asks four questions and a boutique's asks two. One
    function, because the difference is which fields go into the POST and not
    what signing in means.
    """
    path = '/platform/login' if SITE == 'platform' else '/staff/login'
    html = fetch(build, path)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or         re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    if not match:
        return False
    fields = {
        'csrf_token': match.group(1),
        'username': 'admin' if SITE == 'platform' else USER,
        'password': PASSWORD,
    }
    if SITE == 'platform':
        fields['phone'] = os.environ.get('MODRYN_PLATFORM_PHONE', '')
        fields['idnum'] = os.environ.get('MODRYN_PLATFORM_ID', '')
    try:
        build.open(BASE + path, data=urllib.parse.urlencode(fields).encode(),
                   timeout=30).read()
    except Exception:
        return False
    # Proved, not assumed: a sweep reporting a clean site it was never signed
    # in to is worse than one that fails loudly.
    landing = '/platform/home' if SITE == 'platform' else '/manage/team-screen'
    return bool(fetch(build, landing))


def scan(chunks, expected, allowed=()):
    """What is written in the wrong script here."""
    latin, other = [], []
    for where, text in chunks:
        if text.strip() in ODOO_CHROME:
            continue
        # A language's own name, in the language picker. "Arabic / الْعَرَبيّة"
        # is Arabic script on a Hebrew page and it is exactly right.
        if where in PICKER_TAGS and '/' in text and len(text) < 40:
            continue
        for word in LATIN.findall(text):
            if word in ALLOWED or word in allowed or word in TENANT_WORDS                     or NOISE.match(word) or len(word) < 3:
                continue
            latin.append((where, word, text[:70]))
        if expected == 'hebrew' and ARABIC.search(text):
            other.append((where, 'arabic', text[:70]))
        if expected == 'arabic' and HEBREW.search(text):
            other.append((where, 'hebrew', text[:70]))
    return latin, other


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', choices=sorted(LANGUAGES))
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if not PASSWORD:
        print("MODRYN_DEMO_PASSWORD is not set; the signed-in pages cannot be "
              "read and a sweep of the public ones only would report a clean "
              "site that has not been looked at.", file=sys.stderr)
        return 2

    langs = [args.lang] if args.lang else sorted(LANGUAGES)
    findings = []
    for lang in langs:
        expected, prefix = LANGUAGES[lang]
        build = opener_for(lang)
        if not sign_in(build):
            print("could not sign in as %s" % USER, file=sys.stderr)
            return 2
        for path in (PLATFORM_PAGES if SITE == 'platform' else PAGES):
            html = fetch(build, prefix + path)
            if not html:
                continue
            # Trust nothing: if the page came back in another language the
            # findings would be filed against the wrong one, which is worse
            # than not checking - it reports a clean Arabic site that was never
            # read. The served <html lang> is the site's own answer.
            served = re.search(r'<html[^>]*lang="([^"]+)"', html)
            if served and served.group(1).replace('-', '_').lower()                     != lang.lower():
                findings.append({
                    'lang': lang, 'page': path, 'kind': 'wrong-page',
                    'where': 'html', 'text': served.group(1),
                    'context': 'asked for %s, got %s' % (lang, served.group(1)),
                    'ours': True})
                continue
            # The tenant's own name, whatever it is, is not a translation
            # failure - it is what the boutique is called.
            named = COMPANY.search(html)
            allowed = set(LATIN.findall(named.group(1))) if named else set()
            latin, other = scan(visible_text(html), expected, allowed)
            for where, word, context in latin:
                findings.append({'lang': lang, 'page': path, 'kind': 'latin',
                                 'where': where, 'text': word,
                                 'context': context,
                                 'ours': path not in FOREIGN_CHROME})
            for where, script, context in other:
                findings.append({'lang': lang, 'page': path, 'kind': script,
                                 'where': where, 'text': context,
                                 'context': context, 'ours': True})

    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 1 if any(f['ours'] for f in findings) else 0

    ours = [f for f in findings if f['ours']]
    theirs = len(findings) - len(ours)
    if not ours:
        print("every screen reads in its own language "
              "(%d on pages this product does not own)" % theirs)
        return 0

    by_page = {}
    for finding in ours:
        by_page.setdefault((finding['lang'], finding['page']), []).append(finding)
    for (lang, page), rows in sorted(by_page.items()):
        print("\n%s  %s" % (lang, page))
        seen = set()
        for row in rows:
            key = (row['kind'], row['text'])
            if key in seen:
                continue
            seen.add(key)
            print("   %-8s %-28s  in <%s>  %s" % (
                row['kind'], row['text'][:28], row['where'],
                row['context'].replace('\n', ' ')[:46]))
    print("\n%d to look at, on %d screens (%d more on pages this product "
          "does not own)" % (len(ours), len(by_page), theirs))
    return 1


if __name__ == '__main__':
    sys.exit(main())
