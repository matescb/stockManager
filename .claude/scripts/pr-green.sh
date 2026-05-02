#!/usr/bin/env bash
# pr-green.sh — list open PRs that the cron-mode watcher should act on.
#
# Output (one PR per line, tab-separated):
#   <number>\t<headRefOid>\t<title>
#
# A PR is "green" — i.e. eligible for action — when ALL of the following
# hold (matches the green-only rule baked into pr-watcher.md):
#
#   1. not draft, not from a fork
#   2. mergeable == "MERGEABLE" (UNKNOWN / CONFLICTING are out)
#   3. every status check except the legacy `review` / `claude-review`
#      one resolves to SUCCESS / SKIPPED / NEUTRAL — anything else
#      (PENDING / IN_PROGRESS / FAILURE / CANCELLED / etc.) disqualifies
#   4. no existing PR comment carries the sticky marker
#      `<!-- claude-review:<headRefOid> -->` (i.e. not already reviewed
#      at this exact SHA)
#
# Single `gh pr list` round-trip — much cheaper than per-PR querying.
#
# Usage:
#   ./.claude/scripts/pr-green.sh                    # plain list
#   ./.claude/scripts/pr-green.sh | wc -l            # count
#   ./.claude/scripts/pr-green.sh | cut -f1          # numbers only
#
# Exit 0 with empty stdout when nothing qualifies (the common quiet case).

set -euo pipefail

# `--json comments` is the expensive field; gh fetches it lazily so
# requesting it for every PR is fine for the queue sizes we deal with
# (sub-100 open PRs). If it ever becomes slow, drop --json comments
# here and re-check the marker per-candidate at the end.
gh pr list --state open --limit 100 \
  --json number,headRefOid,title,isDraft,isCrossRepository,mergeable,statusCheckRollup,comments \
  --jq '
    .[]
    | select(.isDraft == false)
    | select(.isCrossRepository == false)
    | select(.mergeable == "MERGEABLE")
    | . as $pr
    | (
        # Every non-legacy check must be in the passing set.
        # CheckRun objects expose .conclusion (after .status==COMPLETED);
        # StatusContext objects expose .state. Coalesce both to one value
        # then test membership in {SUCCESS, NEUTRAL, SKIPPED}.
        [
          .statusCheckRollup[]?
          | select(((.name // .context // "") | ascii_downcase) as $n
                   | $n != "review" and $n != "claude-review")
          | (.conclusion // .state // "PENDING")
        ]
        | length > 0
        and all(. == "SUCCESS" or . == "NEUTRAL" or . == "SKIPPED")
      ) as $checks_ok
    | select($checks_ok)
    | (
        # Sticky-marker dedup — skip PRs already reviewed at this SHA.
        [.comments[]?.body | select(contains("<!-- claude-review:" + $pr.headRefOid + " -->"))]
        | length == 0
      ) as $unmarked
    | select($unmarked)
    | [.number, .headRefOid, .title] | @tsv
  '
