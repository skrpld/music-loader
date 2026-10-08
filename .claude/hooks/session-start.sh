#!/bin/bash
# Installs the CLI with its test dependencies (pytest, ruff) into cli/.venv and
# puts that venv first in PATH, so `pytest cli/tests` and `ruff check cli` work
# in a fresh Claude Code cloud session.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cli_dir="$CLAUDE_PROJECT_DIR/cli"
venv="$cli_dir/.venv"

if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
fi
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check -e "$cli_dir[dev]"

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$venv\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
