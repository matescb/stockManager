# ADR-0036: Part documents extend the datasheet fetch policy to a cron-only `document` kind

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-11-24
- **Supersedes**: —
- **Superseded by**: —
- **Extends**: [ADR-0033](0033-datasheet-fetch-drops-host-allow-list.md)

## Context

ADR-0033 let the hourly `datasheet-backfill` cron job fetch a part's
datasheet from any manufacturer host, because the Mouser/DigiKey allow-list
localised almost nothing, and moved the weight onto a pinned resolved IP,
HTTPS only, no redirects, a 10 MB cap, a wall-clock budget, magic bytes,
PDF only, a real User-Agent and a per-host throttle. Request handlers kept
the allow-list.

Agents working on a part need more than the datasheet: the application
notes, technical notes, PCB design notes, programming / reference manuals
and PCNs. DigiKey already lists them in the product record's `MediaLinks`,
and an agent reading a manufacturer site finds others. They live on the
same manufacturer hosts the datasheets do. The owner decided (2026-11-24):

- agents must be able to download a part's local PDFs; no text extraction
  yet (Datalab later — `part_datasheets.derived` stays untouched);
- DigiKey media links of those document types are downloaded locally,
  PDF only;
- agents may queue documents they find themselves.

## Decision

1. **A second unrestricted kind, `document`.** `assets._UNRESTRICTED_KINDS`
   is `{"datasheet", "document"}`. The relaxation still needs BOTH the kind
   AND an explicit `allow_any_host=True`, and every ADR-0033 control applies
   to `document` unchanged — including PDF-only and the throttle being
   gated on the opt-in.
2. **Still cron-only.** The only callers that pass `allow_any_host=True`
   are the two halves of the same `datasheet-backfill` job:
   `services/datasheets.py` (datasheets, from the `datasheet_url` custom
   field, as before) and `services/documents.py::backfill_documents`
   (everything else). Both run in `backend-cron-datasheets` under one
   session advisory lock, one per-run host breaker and one run budget
   (`datasheets.RUN_BUDGET_SECONDS`, 500 s — no fetch starts after it, so a
   run stays inside the sidecar's `timeout 600`). No request handler and no
   MCP tool fetches; `tests/test_datasheet_fetch_policy.py` scans the source
   for both.
3. **Queuing is a DB write.** Documents reach the queue two ways, neither of
   which touches the network:
   - the provider create / refresh paths map DigiKey `MediaLinks` to
     `record["media_links"]` (documents only: application notes / design
     resources, technical notes and white papers, PCB design guides,
     programming / user / reference manuals, PCNs; photos, videos, EDA / CAD
     / 3D models, HTML datasheets, training and catalogue pages are dropped)
     and `documents.queue_media_links` writes `pending` rows;
   - the MCP write tool `request_part_document` validates the URL's shape
     (https, a public-looking hostname — no IP literal, no localhost or
     internal suffix, a letter TLD — no userinfo, default port, ≤ 2048
     chars), dedups, enforces a per-workspace cap on waiting requests
     (`DOCUMENT_REQUEST_PENDING_CAP`, 200), writes a `pending` row and an
     audit row carrying host + kind only.
4. **One record table.** `part_datasheets` gains `kind`, `title` and
   `origin` (`datasheet_field` | `media_link` | `agent`) and the `pending`
   status (migration 0086). The idempotency key (workspace, part, URL
   hash), the attempt / cooldown / terminal-failure bookkeeping, the
   content-addressed storage and the attachment registration are shared;
   a stored document is an `Attachment` whose `file_type` is its kind.
5. **Agents download through the existing attachment route.**
   `list_part_documents` returns each PDF's absolute
   `{APP_BASE_URL}/api/attachments/{id}/download`, which already accepts a
   Bearer API token (a GET, so a read-only token is enough), is
   workspace-scoped, and always answers `Content-Disposition: attachment`.

## Consequences

- **Good**: agents get the paperwork for a part as local files with
  provenance (`source_url`, `retrieved_at`, `sha256`), without the backend
  ever opening a connection from a request; one job, one lock, one set of
  controls to reason about.
- **Bad**: the unrestricted surface is wider — it now covers every PDF an
  agent with a write token asks for, not only datasheet URLs a provider
  supplied. The pinned-IP check is what keeps that off internal addresses;
  the URL-shape validation at queue time is defence in depth, not the
  control.
- **Bad**: DigiKey's `MediaType` taxonomy is coarse ("Design Resources",
  "Other Related Documents"), so the kind is partly inferred from the
  title. A misfiled kind is cosmetic; a dropped document can still be
  queued by an agent.
- **Neutral**: a provider dropping a media link later does not dequeue it;
  the row is fetched or fails on its own.

## Alternatives considered

- **A `media_links` JSON catalog custom field.** Rejected: `custom_fields.value`
  is `varchar(1024)` and provider values are truncated to it, so a list of
  URLs would be cut mid-JSON for any part with more than a few documents,
  and it would need its own namespace / unlink handling for a value nothing
  renders. The links go straight to `pending` rows instead.
- **Fetching on the request path for agent requests.** Rejected for the
  ADR-0033 reasons: a single uvicorn worker, and no arbitrary host from a
  request.
- **A sibling `part_documents` table.** Rejected: it would copy every
  column and the whole retry machinery of `part_datasheets`.
