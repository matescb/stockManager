#!/usr/bin/env bash
# PreToolUse guard: block in-place edits to alembic migration files that are
# already committed on origin/main. Once a migration is on main it has been
# auto-deployed to prod (CI runs `git reset --hard origin/main` + the backend
# container's CMD runs `alembic upgrade head`), so editing it in place breaks
# the alembic chain on the next deploy. Add a NEW migration instead.
#
# Hook input is JSON on stdin; we read tool_input.file_path. Exit 2 blocks
# the tool call; exit 0 allows it.

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

[[ "$file_path" == *"/backend/alembic/versions/"*".py" ]] || exit 0

repo_root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0
rel=$(realpath --relative-to="$repo_root" "$file_path" 2>/dev/null || echo "$file_path")

# If the file is tracked on origin/main, refuse the edit.
if git -C "$repo_root" cat-file -e "origin/main:$rel" 2>/dev/null; then
    cat >&2 <<EOF
Refusing to edit '$rel'.

This alembic migration is already on origin/main, which means it has been
auto-deployed to prod. Editing it in place will break the alembic chain
on the next \`alembic upgrade head\`.

If you need to change the schema, add a NEW migration file instead. See
CLAUDE.md → "Migrations" for the naming convention.

If you genuinely need to override (rare — e.g. fixing a typo before the
next merge), bypass via the Bash tool with \`git\` directly.
EOF
    exit 2
fi

exit 0
