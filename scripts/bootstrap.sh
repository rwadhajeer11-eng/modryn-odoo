#!/usr/bin/env bash
# Idempotent bootstrap: Odoo source + Python env + RTL toolchain.
# Safe to re-run; each step skips if already done.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# 1. Odoo source — shallow clone, gitignored, never edited.
if [ ! -d odoo/.git ]; then
  echo "==> cloning Odoo 19.0 (shallow)"
  git clone --depth 1 --branch 19.0 https://github.com/odoo/odoo.git odoo
else
  echo "==> odoo/ present, skipping clone"
fi

# 2. Python env. Odoo 19 wants 3.10-3.12; 3.13 is not supported by all deps.
if [ ! -d .venv ]; then
  echo "==> creating venv (python3.12)"
  python3.12 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# psycopg2 builds from source on macOS and needs pg_config on PATH.
export PATH="$(brew --prefix postgresql@16)/bin:$PATH"

echo "==> installing requirements"
pip install -q -U pip wheel
pip install -q -r odoo/requirements.txt

# 3. RTL toolchain. Odoo generates its RTL stylesheets by running rtlcss over
# the compiled LTR CSS. Without it, a Hebrew/Arabic site renders LTR-ish.
if ! command -v rtlcss >/dev/null 2>&1; then
  echo "==> installing rtlcss"
  npm install -g rtlcss
else
  echo "==> rtlcss present"
fi

echo
echo "Bootstrap complete. Next: ./scripts/build_template.sh"
