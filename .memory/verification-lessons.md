# How the verification lied

On this project the test harness produced more false readings than the code produced real bugs.
Each entry below caused a wrong conclusion to be stated out loud before being caught.

**The rule that came out of it:** when a measurement disagrees with what the code obviously
says, suspect the measurement first.

---

## False confidence — the dangerous kind

### An anonymous check proves the gate, never the page

`/floor` returning `303` to a logged-out visitor says only that the guard works. It says nothing
about whether the page renders for the people who are allowed in.

`/floor` was returning **500 for every signed-in manager** — a non-stored field used in a search
domain — while every check in the suite stayed green.

**Fix.** `verify.sh` §10a signs in with a real session and asserts `/floor`, `/atelier` and
`/floor/data` actually render. Any authenticated surface needs an authenticated check.

### A success message proves the code ran, not that data persisted

`configure_twilio.py` printed "configured" from a transaction that was never committed. Odoo's
shell does not autocommit. Read the row back from Postgres.

### Green suites hide silently-unexecuted checks

Related lesson from the sibling project: a broken test *file* reads as one line, not N failures.
Count what ran, not what passed.

---

## False alarms — wasted time

### Comparing a naive-UTC column to psql's local `now()`

`ir_cron.nextcall` is `timestamp without time zone` holding **UTC**. psql's `now()` is
session-local (`Asia/Jerusalem`, UTC+3). Comparing them read every cron as three hours overdue.

**Fix.** `now() at time zone 'utc'`.

**And underneath it, a second, real finding:** short-interval crons genuinely *are* overdue
between firings, because Odoo's scheduler passes each database only about every 60 seconds. Two
distinct defects with one symptom — fixing the first exposed the second. The assertion was wrong
twice, for different reasons, and the first "fix" was right for the wrong reason.

### Capturing a page into a variable and echoing it back

This was recorded for a long time as "`$(curl …)` mangles large bodies". **That is wrong**, and
it was measured wrong: `$( )` is byte-faithful — capturing a 950,001-byte file and re-emitting it
with `printf '%s'` returns 950,000 bytes, losing only the trailing newline it documents.

The real culprit is **zsh's `echo`, which interprets backslash escapes**. A CSS or JS body
containing `\n`, `\t` or `\0` comes back altered, so a grep over it returns a false zero. This
once "proved" the Arabic storefront was broken when it was fine.

```zsh
V=$(cat page.html)
echo "$V"    | grep -c 'x'   # backslashes interpreted — unreliable
printf '%s' "$V" | grep -c 'x'   # faithful
```

**Fix.** Fetch to a file and grep the file — `verify.sh` has `fetch()` for exactly this. If you
must hold a body in a variable, emit it with `printf '%s'`, never `echo`.

### `create_date` is readonly, so backdating a row via the ORM does nothing

`record.write({'create_date': ...})` to age a record for an escalation test is **silently
ignored** — no error, and the test then "proves" escalation is broken.

**Fix.** Backdate with SQL: `update … set create_date = now() at time zone 'utc' - interval '45 seconds'`.

### `str.replace()` no-ops silently when the target has drifted

A patch script printed its success message unconditionally while the replacement never matched,
so a template edit appeared to apply and had not. **Assert that the string changed**
(`assert s != original`) or grep the file afterwards.

---

## Harness papercuts that cost real time

### Internal users get `data-oe-*` attributes injected into forms

A page rendered for an **internal** user (the owner) has Odoo's website-editor attributes
injected *between* `name=` and `value=`:

```html
<input type="hidden" name="csrf_token" data-oe-model="ir.ui.view" … value="8f65…"/>
```

A `name="csrf_token" value="([^"]*)"` regex finds nothing and every POST 400s. Portal users'
pages are clean, which is why it only ever broke on owner pages.

**Fix.** Match the tag first, then the value:

```bash
grep -oE '<input[^>]*name="csrf_token"[^>]*>' page.html | head -1 | grep -oE 'value="[^"]*"'
```

### `psql -tAc` with `RETURNING` also prints its status line

`psql -tAc "insert … returning id"` emits `8` **and** `INSERT 0 1`. Capturing that into a shell
variable yields a multi-line string that silently corrupts the next JSON payload built from it.

**Fix.** Pipe through `head -1`, or create the row in an Odoo shell script that prints exactly
what you want.

### zsh does not word-split unquoted variables, and eats bare globs

The shell here is **zsh**, not bash. Two failures follow, and both look like real findings:

```zsh
SCSS=$(git ls-files | grep 'scss$')
grep -l "\.modryn_chip" $SCSS      # zsh passes ALL filenames as ONE argument
```

Every class came back "MISSING". They all exist. Pipe through `xargs` instead:

```zsh
git ls-files | grep 'scss$' | xargs grep -l "\.modryn_chip"
```

Separately, `grep -r pattern dir --include=*.scss` fails with `no matches found` because zsh
expands the bare `*.scss` before grep sees it. Quote it: `--include='*.scss'`.

Both produce a confident, wrong, negative result. Whenever a check reports that something
obvious is absent, re-run it a second way before believing it.

### Playwright MCP is rooted to the MODRYN repo

Screenshots and accessibility dumps land in `/Users/mrwen/Documents/Github/Ryan + rawad + mrwen`
— the repo that must stay untouched. Absolute paths elsewhere are denied.

**Fix.** Move each artefact out immediately after taking it, then re-check that repo is clean.
Do not `git add` a whole directory afterwards without looking: a wildcard add swept ~300
transient `.yml` dumps into a commit here. They are now gitignored.

### The e2e/asset trap

Anything served from a build output tests the **last build**, not your edit. Rebuild first, or
your fix never ran. In Odoo's case the equivalent is: module data is only re-read on `-u`, and
compiled assets are cached in memory — restart after editing.

---

## What a trustworthy check looks like here

- Reads state back from **Postgres**, not from the response that claimed to write it.
- Signs in as the role that actually uses the surface.
- Fetches pages to a file.
- Compares UTC to UTC.
- Fails loudly when its own precondition is missing, rather than passing vacuously.
- States what it did **not** cover.
