#!/usr/bin/env bash
# Stop hook: when Claude's turn ends, if any web/ TypeScript files have
# uncommitted changes, run `tsc -b` once to type-check the composite
# project. Surfaces errors only on failure; silent on success. Amortises
# the ~10s tsc cost across however many edits happened in the turn.
#
# Skips silently if web/node_modules isn't installed (no-op until the
# user runs `npm install` or `npm ci`).

set -euo pipefail

# Drain stdin (Claude Code passes JSON; we don't need any of it).
cat >/dev/null 2>&1 || true

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

# Run only if there are uncommitted .ts/.tsx changes under web/. If the
# turn didn't touch any TypeScript, this turn doesn't need a type-check.
changed=$(git -C "$repo_root" status --porcelain -- web/ 2>/dev/null | grep -E '\.tsx?$' | head -1 || true)
[[ -n "$changed" ]] || exit 0

TSC="$repo_root/web/node_modules/.bin/tsc"
[[ -x "$TSC" ]] || exit 0

out=$(cd "$repo_root/web" && "$TSC" -b 2>&1) || rc=$?
rc=${rc:-0}

if [[ $rc -ne 0 ]]; then
    echo "[tsc -b] type errors in uncommitted web/ changes:" >&2
    echo "$out" | tail -40 >&2
fi

exit 0
