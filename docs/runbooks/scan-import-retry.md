# Scan-import retry runbook

Audience: engineer / on-call

## When

A user reports that `POST /api/parts/bulk-import-from-scan` timed out, returned a transient provider error, or the browser lost the response after submitting a scan-import queue.

## Severity

Usually SEV-3. Escalate to SEV-2 if multiple workspaces are blocked from importing stock.

## TTR

15-30 minutes for a single workspace once you know whether the original request committed.

## Pre-flight

- Ask whether the retry is from the original browser queue. The frontend sends an explicit `idempotency_key`; retrying from the same queue is the safest path.
- If support is replaying a captured payload manually, check whether it includes `idempotency_key`.
- If there is no `idempotency_key`, preserve the exact row order from the original request. The fallback content key is order-sensitive.

## Steps

1. If the original payload has an `idempotency_key`, retry the same payload with the same key.
2. If the original payload has no `idempotency_key`, do not sort, group, dedupe, or otherwise reorder `rows` before replaying it.
3. Before replaying a shuffled or edited payload, inspect the affected MPNs and `bag_signature` values in the workspace. A different row order changes the fallback content key and can bypass the idempotency cache.
4. If the original request likely committed, prefer having the user re-open the scan queue and resolve duplicate / bag-rescan rows instead of manually replaying.

## Verify

- The response summary matches the expected created / duplicate / failed counts.
- No unexpected duplicate active parts were created for the same workspace MPNs.
- Re-scanned bags return `bag_rescan` rather than creating new stock rows for the same physical bag.

## Rollback

There is no bulk undo. If a replay double-imported stock, correct it with normal stock removal / adjustment flows so the ledger keeps an auditable trail.

## Post-mortem

Record whether the retry used the explicit `idempotency_key`. If not, include whether row order changed and link to the affected workspace / request id.
