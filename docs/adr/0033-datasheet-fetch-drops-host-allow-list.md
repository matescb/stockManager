# ADR-0033: Datasheet fetches drop the host allow-list and pin the resolved IP instead

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-09
- **Supersedes**: the datasheet half of SEC2-006's host allow-list
- **Superseded by**: —

## Context

`backend/app/domain/parts/services/assets.py` downloads provider-supplied
images and datasheets into our own `UPLOAD_DIR` so the app keeps working when
a vendor CDN rotates a URL. SEC2-006 hardened that helper against SSRF with a
host allow-list of eight Mouser and DigiKey hostnames, plus an
`ip.is_global` DNS check, `follow_redirects=False`, a 10 MB streaming cap and
magic-byte validation.

The allow-list worked exactly as designed, and that is the problem. Measured
in prod before this change:

- **257** parts carry a `datasheet_url` custom field.
- **249** of those point at an external URL; only **8** already pointed at our
  own asset route.
- Those 249 span **40+ manufacturer domains** — vishay 28, ti 21, we-online 17,
  analog 17, panasonic 17, yageo 16, murata 11, nexperia 8, molex 7, te 6,
  st 6, microchip 6, diodes 5, tdk 4, onsemi 4, and a long tail.
- Only **7** were on an allow-listed host.
- **5** PDFs existed on disk, and the `attachments` table had **zero** rows.

So in practice datasheets were not stored locally at all. Every datasheet link
in the app was a hot link to a manufacturer site that can rotate, paywall, or
disappear. The product goal that motivates this work — convert datasheets to
markdown/JSON so agents can read them — needs the bytes, and the bytes are on
those 40+ domains.

Adding 40 hostnames to the allow-list is not a fix. It would be stale on the
next vendor CDN rename, it would not cover the next part someone adds, and it
would give a false sense that the list is doing security work when its real
function had become "block almost everything, including the legitimate case".

Disk is not a constraint: ~250 PDFs is roughly 500 MB against 72 GB free.

## Decision

**For datasheets fetched by the offline backfill, the host allow-list is
removed.** The relaxation requires two conditions together: `kind` in
`_UNRESTRICTED_KINDS` (today only `"datasheet"`) **and** the caller passing
`allow_any_host=True`. Everything else keeps the original narrow
Mouser/DigiKey allow-list unchanged.

The opt-in is load-bearing, not ceremony. The only caller that passes it is
the `datasheet-backfill` cron job. The request-path callers — provider
import and provider refresh — call `fetch_provider_asset`, which never opts
in. So a user-triggered HTTP request can neither make the backend open a
connection to a host outside the allow-list, nor be made to block the single
uvicorn worker (ADR-0012) for a 10s timeout plus a per-host throttle against
an arbitrary vendor. A newly imported part gets its datasheet within the hour
from the sidecar instead of inline.

Widening the image path was not asked for and would enlarge the blast radius
for no product gain.

The allow-list's security weight moves onto controls that do not depend on
enumerating vendors. In particular, one of those controls had to be *fixed*
first, because it was only sound while the allow-list existed:

**Pinned resolution.** The old `_host_is_allowed` called
`socket.gethostbyname(host)`, checked `ip.is_global`, and then handed the
original **hostname** to httpx, which resolved it **again** when connecting.
That is a textbook DNS-rebinding window: a hostile authoritative server can
answer with a public address for the check and a private one for the connect.
With the allow-list in front of it that window was mostly theoretical — the
attacker had to control DNS for `media.mouser.com`. Without the allow-list the
IP check becomes the *primary* control, so the window had to close.

`_resolve_pinned_ip` now:

1. resolves the hostname **once** with `socket.getaddrinfo`;
2. rejects the host if **any** returned address fails
   `_ip_is_publicly_routable` — "any", not "all", so a resolver answering
   `[93.184.216.34, 127.0.0.1]` cannot smuggle a private address into the set;
3. returns the address to connect to.

The request is then issued against a URL whose authority is that **IP
literal**, with `Host:` set to the original host and the `sni_hostname` httpx
extension set to the original hostname. httpcore passes `sni_hostname`
straight through as `server_hostname`, so TLS still verifies the certificate
against the real name. There is no second resolution, so check and connect
cannot disagree.

`_ip_is_publicly_routable` unwraps IPv4-mapped IPv6 (closing
`::ffff:169.254.169.254`), strips IPv6 zone ids, and rejects private,
loopback, link-local (the 169.254/16 cloud-metadata range), multicast,
reserved and unspecified addresses in addition to `is_global`.

The compensating controls that now carry the weight, in full:

| Control | What it stops |
| --- | --- |
| Pinned resolved IP + `is_global` validation of every A/AAAA answer | Reaching RFC1918, loopback, link-local, and cloud metadata (169.254.169.254), including via DNS rebinding between check and connect |
| **HTTPS only** on the unrestricted path | A plaintext MITM choosing the response; without an allow-list, cert verification is what proves we reached the host the URL named |
| `follow_redirects=False` (a 30x is a refusal) | A vendor 302 pointing at an internal address, which would void every check above |
| No `user:pass@host` URLs | Forwarding a credential embedded in a stored URL to an arbitrary third party |
| 10 MB cap: `Content-Length` pre-check + mid-stream chunk-counter abort | Memory exhaustion from a hostile multi-GB body |
| Magic-byte validation against the Content-Type-derived extension | A compromised CDN serving something that is not the file type it claims |
| Datasheets must sniff as PDF; anything else is refused | A vendor URL that 200s with an HTML landing page filling the store with junk, and any non-PDF payload reaching disk on the unrestricted path |
| No SVG (on the image path `image/svg+xml` and `.svg` land as `.bin`, served `attachment`; on the datasheet path they are refused outright) | Stored-XSS through inline-rendered SVG |
| Per-host throttle (`ASSET_FETCH_MIN_HOST_INTERVAL_SECONDS`, default 2s) — **backfill path only** | A 250-URL backfill hammering one vendor and earning a block |
| Wall-clock budget (`_MAX_WALL_CLOCK_SEC`, 30s) — both paths | A host dribbling one chunk every 9s, which resets httpx's per-operation read timer forever and never trips it |
| Redacted logging (`scheme://host/path`, no query string) | Signed-URL tokens leaking into logs |
| The unrestricted path needs an explicit `allow_any_host=True`, passed only by the cron backfill — never by a request handler, and never from a user-supplied URL parameter | Turning the helper into an open request proxy, and tying up the single uvicorn worker on a vendor's timeout |

Note the last row: this helper is not an endpoint. Nothing accepts a URL from
a request and fetches it, and the unrestricted policy is not reachable from a
request handler at all. The URLs it sees come from a provider API response or
from a `datasheet_url` custom field, so the attacker model is "a compromised
or hostile upstream provider", not "any authenticated user".

TLS verification is never relaxed anywhere in this path — no `verify=False`
(ADR-0008).

## Consequences

- **Good**: Datasheets actually localise. The ~249 external URLs become files
  we own, registered as `attachments` rows, ready for the planned Datalab
  conversion.
- **Good**: The DNS-rebinding window between check and connect is closed for
  *both* policies, including the allow-listed image path — that fix is
  strictly additive there.
- **Trade-offs**: The set of hosts the backend will open a TLS connection to
  is now "any public host that some provider or operator put in a
  `datasheet_url` field". That is a real widening. It is bounded by the
  controls above and by the fact that the response is never rendered as
  anything but a downloadable PDF/image.
- **Trade-offs**: A vendor that blocks datacentre IPs will return 403; that
  part gets a `failed` row with `failure_code=http_403` and is retried on the
  configured cooldown rather than forever.
- **Trade-offs**: The backfill commits **per candidate** rather than once per
  run. That is deliberate: `run_job` wraps a job in one transaction, so a
  `timeout 600` kill would roll back every `attempts` increment the run made,
  and the deterministic candidate ordering would hand back the same URL first
  next time — a sweep that never advances. The cost is that `run_job`'s
  `pg_try_advisory_xact_lock` is released at the first commit, so the job
  takes its own SESSION-level lock
  (`DATASHEET_BACKFILL_LOCK_CLASSID`) for the duration.
  Worst case per candidate is resolution + the 2s throttle + the 30s
  wall-clock budget, so at the shipped batch of 12 a run is bounded at ~444s,
  inside the sidecar's `timeout 600`. Raising the batch past ~15 means a run
  can be killed mid-sweep; survivable, but it wastes the in-flight fetch.
- **What it forbids**:
  - Do not add another `kind` to `_UNRESTRICTED_KINDS` without a new ADR.
  - Do not pass `allow_any_host=True` from a request handler. If a route ever
    needs a datasheet fetched on demand, that needs its own decision about
    worker blocking and about who supplies the URL.
  - Do not restore hostname-based connection (`client.get(url)` on the
    original URL) — the pinned-IP request is the control, and re-resolving
    reopens the rebinding window.
  - Do not set `follow_redirects=True` to "fix" a vendor that 302s. A 30x is
    a refusal by design.
  - Do not relax the unrestricted path to allow plain HTTP.
  - Do not remove the per-host throttle to make the backfill faster.
  - Do not apply the per-host throttle to the allow-listed (request-path)
    callers. It is a blocking sleep;
    `POST /api/parts/bulk-import-from-scan` fetches up to 50 images from a
    single provider CDN host inside one request with a 60s deadline, and
    `refresh-from-provider` is a sync route on a single uvicorn worker.
  - Do not treat `_TIMEOUT_SEC` as a request budget. It is httpx's
    per-operation timeout; `_MAX_WALL_CLOCK_SEC` is the budget.
  - Do not go back to one commit per backfill run.
  - Do not expose a route that takes a URL and calls `fetch_asset` with it.

## Storage shape

A stored datasheet is recorded in three places, deliberately:

1. **On disk**, content-addressed at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}` —
   the existing invariant (ADR-0005), unchanged, served by the existing
   `GET /api/parts/assets/{ws_id}/{filename}` route.
2. **As an `attachments` row** with `file_type='datasheet'`, `object_type='part'`.
   This makes it a first-class object: it lists and downloads through the
   existing attachments API and it inherits the polymorphic-cleanup
   `before_delete` listeners, so a part hard-delete removes it. `attachments`
   had zero rows before this change, so this is the first real user of that
   path; `tests/test_datasheet_backfill.py` pins the cleanup.
3. **As a `part_datasheets` row** (migration 0079), one per
   (workspace, part, source URL). This is the fetch bookkeeping the other two
   cannot hold: `status`, `attempts`, `failure_code`, `last_attempt_at` —
   which is what makes the backfill resumable and idempotent.

Because content is addressed by hash, two parts sharing a family datasheet
share one file. The attachment delete route is therefore refcount-aware: it
unlinks the file only when no other attachment in the workspace references
the same `storage_key`.

**The backfill never rewrites the part's `datasheet_url` custom field.** That
field is provider-owned on linked parts and a provider refresh reconciles it;
if the backfill wrote a local path there, the two would fight on every
refresh. The custom field stays the upstream provenance record.

**Forward slot for Datalab.** `part_datasheets.derived` (JSONB) and
`derived_status` exist so the planned PDF → markdown/JSON + extracted images
conversion needs no further migration: the converted artifacts become further
`attachments` rows on the same part (`file_type='datasheet_markdown'`,
`'datasheet_image'`, …) and `derived` holds the manifest tying them together
(their attachment ids, converter version, page count). This PR does not
implement the Datalab HTTP integration.

`attachment_id` being nullable has one consequence worth stating: a row whose
attachment was deleted is **no longer settled**. `_candidate_rows` requires
`status = 'stored' AND attachment_id IS NOT NULL`, so deleting a datasheet
attachment (which also unlinks the file) puts the part back in the sweep after
its cooldown rather than leaving a `stored` row pointing at nothing.

## Backfill

`datasheet-backfill` is registered in the `run_job` allow-list and runs in a
new `backend-cron-datasheets` sidecar — a new cadence means a new sidecar,
never a second scheduler (ADR-0021). It is the only cron sidecar that mounts
the `uploads` volume, because it is the only one that writes files.

- Bounded batch (`DATASHEET_BACKFILL_BATCH_SIZE`, default 12) so one run
  finishes inside the sidecar's `timeout 600`; ~249 backlogged URLs clear in
  about a day.
- Commits per candidate, under its own session-level advisory lock, so a
  killed run keeps every attempt counter it earned.
- A `stored` row is never re-downloaded.
- A failure writes a `failed` row with an incremented attempt count and starts
  a cooldown (`DATASHEET_BACKFILL_RETRY_AFTER_SECONDS`), capped by
  `DATASHEET_BACKFILL_MAX_ATTEMPTS`. One bad URL cannot stall the batch.
- `DATASHEET_BACKFILL_INTERVAL_SECONDS=0` disables it; the sidecar parks on
  `sleep infinity` and stays healthy, mirroring `backend-cron-sessions`.
- Every successful store or recorded failure writes an `audit_log` row. The
  comment carries the **hostname only** — never the path or query string,
  where a signed-URL token would live (ADR-0025 / CLAUDE.md).

## Operating it

`part_datasheets.failure_code` is a short stable token, never free text and
never a URL, so "why did this not localise" is one query:

```sql
SELECT failure_code, count(*)
  FROM part_datasheets
 WHERE workspace_id = :ws AND status = 'failed'
 GROUP BY 1 ORDER BY 2 DESC;
```

| Code | Meaning |
| --- | --- |
| `http_<status>` (e.g. `http_404`, `http_403`, `http_503`) | Upstream said so. 403 is usually a vendor blocking datacentre IPs. |
| `redirect_refused` | Upstream 30x'd. We never follow one — see above. |
| `scheme_not_https` | The stored URL is plain `http://`. The unrestricted path is HTTPS-only, so these need the URL corrected upstream (or a product decision to allow them). |
| `ip_not_public` | The hostname did not resolve, or resolved to a non-public address. |
| `credentials_in_url` | The stored URL embeds `user:pass@`. |
| `unexpected_type` | 200, but the body is not a PDF — usually an HTML landing page. |
| `magic_mismatch` | Declared a type the leading bytes contradict. |
| `too_large` | Over the 10 MB cap. |
| `timeout`, `network_error` | Transient; retried after the cooldown. |
| `too_slow` | The fetch ran past the 30s wall-clock budget — typically a host dribbling bytes just fast enough to keep httpx's read timer alive. |
| `local_wrong_workspace`, `local_file_missing`, `local_path_invalid` | The stored value is an `/api/parts/assets/...` path that does not resolve inside this workspace. |

`failure_code` plus `attempts` and `last_attempt_at` is also how you tell a
transient failure from a settled one: once `attempts` reaches
`DATASHEET_BACKFILL_MAX_ATTEMPTS` the pair is left alone until its URL changes.

## Known limitations

Tracked in issue #916, deliberately out of scope here:

- **Only the first resolved address is tried.** `_resolve_pinned_ip` validates
  every address in the answer but returns `addresses[0]`, so a dead CDN edge
  burns an attempt on a host that is reachable via its second A record.
- **Orphan files.** A run killed between the write and the candidate's commit,
  or a part hard-delete (whose polymorphic cleanup is DB-only), leaves a
  content-addressed file with no row referencing it. Harmless but unbounded.
- **`source_url` duplicates token-bearing URLs.** The row stores the vendor URL
  verbatim because it is the idempotency key against `custom_fields.value`, so
  a signed query string now sits in a second table. Logs and `audit_log` are
  already redacted; this is data-at-rest surface only.

## Alternatives considered

- **Add the 40 manufacturer domains to the allow-list** — rejected. It is
  stale the moment a vendor renames a CDN, does not cover the next part
  anyone adds, and dresses up "block the legitimate case" as security.
- **Keep the allow-list and fetch datasheets through an egress proxy** —
  rejected for now: it moves the same decision to a component we would have
  to build and operate, with no staging environment to shake it out in.
- **Fetch on demand in the request path instead of a backfill** — rejected.
  A user-visible request would then block on a third-party host, and the
  request path is exactly where an SSRF surface is most valuable to an
  attacker. The cron sidecar has no user input.
- **Store the PDF bytes in Postgres** — rejected. The content-addressed
  `UPLOAD_DIR` layout already exists, is already served, is already backed
  up, and ADR-0005 makes it the invariant.
- **Skip `part_datasheets` and use only `attachments`** — rejected. There is
  nowhere in `attachments` to record "we tried this URL and got a 404", so
  the backfill would re-download every dead link on every run.

## References

- Source: `backend/app/domain/parts/services/assets.py`
- Source: `backend/app/domain/parts/services/datasheets.py`
- Source: `backend/alembic/versions/0079_part_datasheets.py`
- Source: `backend/app/cli/run_job.py`
- Tests: `backend/tests/test_datasheet_fetch_policy.py`,
  `backend/tests/test_datasheet_backfill.py`
- Related ADR: [ADR-0005](0005-content-addressed-assets.md)
- Related ADR: [ADR-0008](0008-no-tls-verify-false.md)
- Related ADR: [ADR-0021](0021-periodic-jobs-scheduler.md)
- Related ADR: [ADR-0025](0025-universal-audit-log-policy.md)
- Related ADR: [ADR-0028](0028-hard-delete-policy-and-workspace-trigger-contract.md)
