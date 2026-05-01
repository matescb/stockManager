#!/usr/bin/env bash
# PostToolUse: after an edit/write to backend Python, run pytest --collect-only
# to catch import errors, SQLAlchemy mapper misconfigurations, and the kind of
# silly mistakes that would tank the whole test suite. Fast: a couple of
# seconds. Output is suppressed on success and surfaced only on collection
# failures, so this stays out of the way unless something is actually broken.
#
# Skips silently if no Python with pytest is reachable.

set -euo pipefail

input=$(cat)
file_path=$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception:
    pass
' 2>/dev/null || echo "")

# Only react to backend Python edits.
case "$file_path" in
    */backend/app/*.py|*/backend/tests/*.py) ;;
    *) exit 0 ;;
esac

repo_root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0

# Find a Python with pytest. Try the venv conventions this repo uses, then
# fall back to PATH.
PYTEST=""
for py in \
    /tmp/smv/bin/python \
    "$repo_root/.venv/bin/python" \
    "$repo_root/backend/.venv/bin/python" \
    python3; do
    [[ -x "$(command -v "$py" 2>/dev/null || echo "$py")" ]] || continue
    "$py" -c "import pytest" 2>/dev/null || continue
    PYTEST="$py -m pytest"
    break
done
[[ -n "$PYTEST" ]] || exit 0

# DATABASE_URL is required at import time by app.core.config; supply a dummy
# value — --collect-only never opens a connection.
out=$(cd "$repo_root/backend" && \
      DATABASE_URL='postgresql+psycopg://nope:nope@127.0.0.1:1/nope' \
      $PYTEST --collect-only -q --no-header 2>&1 | tail -40) || rc=$?
rc=${rc:-0}

if [[ $rc -ne 0 ]] || echo "$out" | grep -qE "^ERROR|errors during collection|ImportError|SyntaxError"; then
    echo "[pytest --collect-only] backend collection broke after editing $(basename "$file_path"):" >&2
    echo "$out" >&2
fi

exit 0
