#!/usr/bin/env bash
# lint-delta.sh — run a linter and fail only on NEW violations.
#
# Usage:
#   LINT_CMD="<command>"  BASELINE_FILE="<path>"  bash scripts/lint-delta.sh
#
# The linter output is filtered to lines that look like lint diagnostics
# (i.e. lines matching <file>:<line>:<col>: …) and compared against the
# checked-in baseline. Exit 1 iff there are new violations not in the
# baseline.
#
# To regenerate a baseline after intentional cleanup:
#   See "Updating lint baselines" in docs/development.md

set -euo pipefail

: "${LINT_CMD:?LINT_CMD must be set}"
: "${BASELINE_FILE:?BASELINE_FILE must be set}"
: "${WORK_DIR:-.}"

if [ ! -f "$BASELINE_FILE" ]; then
  echo "ERROR: baseline file not found: $BASELINE_FILE" >&2
  exit 1
fi

TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# Run linter; capture output; ignore its exit code (we decide pass/fail)
(cd "$WORK_DIR" && eval "$LINT_CMD") 2>&1 \
  | grep -E '^[^[:space:]].+:[0-9]+:[0-9]+:' \
  | sed "s|^$(pwd)/||g" \
  | sort > "$TMPFILE" || true

# Find lines present in current output but NOT in baseline
NEW_VIOLATIONS=$(comm -23 "$TMPFILE" <(sort "$BASELINE_FILE"))

if [ -n "$NEW_VIOLATIONS" ]; then
  echo ""
  echo "=== NEW lint violations (not in baseline $BASELINE_FILE) ==="
  echo "$NEW_VIOLATIONS"
  echo ""
  echo "Fix the new violations, or update the baseline if they are intentional:"
  echo "  See docs/development.md — 'Updating lint baselines'"
  exit 1
fi

FIXED=$(comm -23 <(sort "$BASELINE_FILE") "$TMPFILE" | wc -l | tr -d ' ')
CURRENT=$(wc -l < "$TMPFILE" | tr -d ' ')
echo "lint-delta: no new violations (baseline: $(wc -l < "$BASELINE_FILE" | tr -d ' '), current: $CURRENT, fixed since baseline: $FIXED)"
