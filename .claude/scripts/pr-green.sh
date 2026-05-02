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
#   2. author is `matescb` (the human author the watcher is for —
#      dependabot / renovate / other bots are out of scope; bump the
#      author allow-list here if you onboard another human reviewer)
#   3. mergeable == "MERGEABLE" (UNKNOWN / CONFLICTING are out)
#   4. every status check except the legacy `review` / `claude-review`
#      one resolves to SUCCESS / SKIPPED / NEUTRAL — anything else
#      (PENDING / IN_PROGRESS / FAILURE / CANCELLED / etc.) disqualifies
#   5. no existing PR comment carries the sticky marker
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

# GitHub computes `mergeable` lazily — the first API query just KICKS
# OFF the computation, so a fresh `gh pr list` after a main-update will
# return `mergeable=UNKNOWN` for most PRs. The browser hides this:
# rendering the PR page triggers the recompute, so by the time you look
# it's already `MERGEABLE`. The CLI doesn't get that benefit.
#
# Workaround: do TWO passes.
#   Pass 1 — fetch everything; the query itself nudges GH to start
#            computing mergeability for UNKNOWN PRs.
#   Pass 2 — sleep briefly to let GH catch up, then refetch and apply
#            the green-only filter against the now-resolved values.
#
# 4s is enough in practice for the typical queue size; bump if the
# repo grows.
gh pr list --state open --limit 100 --json number,mergeable --jq 'length' > /dev/null
sleep 4

# `--json comments` is the expensive field; gh fetches it lazily so
# requesting it for every PR is fine for the queue sizes we deal with
# (sub-100 open PRs). If it ever becomes slow, drop --json comments
# here and re-check the marker per-candidate at the end.
gh pr list --state open --limit 100 \
  --json number,headRefOid,title,isDraft,isCrossRepository,mergeable,statusCheckRollup,comments,author \
  --jq '
    .[]
    | select(.isDraft == false)
    | select(.isCrossRepository == false)
    | select(.author.login == "matescb")
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
