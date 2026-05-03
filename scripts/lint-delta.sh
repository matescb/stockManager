#!/usr/bin/env bash
# lint-delta.sh — run a linter and fail only on NEW violations.
#
# Usage:
#   LINT_CMD="<command>"  BASELINE_FILE="<path>"  bash scripts/lint-delta.sh
#
# Comparison is line-number-tolerant. We collapse <line>:<col> away on
# both sides and compare multiset counts of (file, rule, message)
# triples — a triple is "new" only when its current count exceeds its
# baseline count. Pure line-shifts (an insert above an existing
# baselined entry that bumps every entry below by N) don't trip the
# gate, which is what bit every PR rebased onto main while the baseline
# pinned exact line numbers.
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

CUR_RAW=$(mktemp)
KEYED_CUR=$(mktemp)
KEYED_BASE=$(mktemp)
trap 'rm -f "$CUR_RAW" "$KEYED_CUR" "$KEYED_BASE"' EXIT

# Capture raw linter output (with line:col) so we can show humans where
# the new violations live.
(cd "$WORK_DIR" && eval "$LINT_CMD") 2>&1 \
  | grep -E '^[^[:space:]].+:[0-9]+:[0-9]+:' \
  | sed "s|^$(pwd)/||g" \
  > "$CUR_RAW" || true

# Collapse helper: <file>:<line>:<col>: <rule> <msg>  ->  <file>: <rule> <msg>
collapse_re='s|:[0-9]+:[0-9]+: |: |'

# Tab-separated current file: <collapsed-key>\t<raw-line>
awk -v re="$collapse_re" '
  { key=$0; sub(/:[0-9]+:[0-9]+: /, ": ", key); print key "\t" $0 }
' "$CUR_RAW" | sort > "$KEYED_CUR"

# Baseline allowance per collapsed key: <key>\t<count>
sed -E "$collapse_re" "$BASELINE_FILE" | sort | uniq -c \
  | awk '{ n=$1; $1=""; sub(/^ /,""); print $0 "\t" n }' > "$KEYED_BASE"

# For each collapsed key in current, accumulate seen[key]; emit raw line
# (with line:col) when seen[key] exceeds baseline allowance.
NEW_VIOLATIONS=$(
  awk -F'\t' '
    NR==FNR { allow[$1] = $2+0; next }
    {
      key = $1; raw = $2
      seen[key]++
      if (seen[key] > allow[key]) print raw
    }
  ' "$KEYED_BASE" "$KEYED_CUR"
)

if [ -n "$NEW_VIOLATIONS" ]; then
  echo ""
  echo "=== NEW lint violations (not absorbed by baseline $BASELINE_FILE) ==="
  echo "$NEW_VIOLATIONS"
  echo ""
  echo "Comparison is line-number-tolerant: a violation only counts as new"
  echo "when its (file, rule, message) triple appears more times than the"
  echo "baseline allows. Pure line-shifts don't trip this gate."
  echo ""
  echo "Fix the new violations, or update the baseline if they are intentional:"
  echo "  See docs/development.md — 'Updating lint baselines'"
  exit 1
fi

BASE_TOTAL=$(wc -l < "$BASELINE_FILE" | tr -d ' ')
CUR_TOTAL=$(wc -l < "$CUR_RAW" | tr -d ' ')
FIXED=$((BASE_TOTAL - CUR_TOTAL))
[ "$FIXED" -lt 0 ] && FIXED=0
echo "lint-delta: no new violations (baseline: $BASE_TOTAL, current: $CUR_TOTAL, fixed since baseline: $FIXED)"
