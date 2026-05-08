# Projects Frontend

Audience: engineer

Project-detail UI flows that span the project tabs, builds, and sourcing routes.

## Source BOM

Project detail and build detail both render the shared `SourceBomButton`. It reads `GET /api/workspaces/current` through `api.get()` and the workspace-scoped `["ws", wsId, "ws", "current"]` query key; when `has_sourcing_company_id` is false, the button is disabled with the `Sourcing not configured` cue instead of linking to the sourcing route. Sources: `web/src/routes/projects/sourcing/SourceBomButton.tsx:18-48`, `web/src/routes/projects/detail/ProjectLayout.tsx:24-29`, `web/src/routes/builds/BuildDetail.tsx:179-186`.

`/projects/:projectId/sourcing` is a lazy child of the project detail route. The page initializes build quantity plus country, currency, and distributor filters from workspace sourcing settings, then posts to `POST /api/projects/{project_id}/sourcing` through `api.post()` with a workspace-scoped TanStack key. Sources: `web/src/App.tsx:98-106`, `web/src/App.tsx:278-285`, `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:390-424`.

The populated response renders three `DataTable`-backed areas: the capacity banner, distributor coverage matrix, and enriched BOM rows. Coverage highlights the best single distributor and best two-distributor combo; BOM rows render authorized stock, best offer, distributor, estimated cost, one risk pill per `risk_flag`, and a `SourcingSourceLabel` column for TrustedParts attribution. Sources: `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:208-252`, `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:254-307`, `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:309-379`.

Status handling is local to the route: loading skeletons, 409 not-configured settings link, 503 budget pause, 502 retry/toast, empty-BOM call to action, and `partial=true` badge are all rendered before the populated tables. Sources: `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:160-206`, `web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:429-547`.

## Purchase Plan Review

The sourcing page can create a purchase plan from the current build quantity, country, currency, and distributor filters. The "Generate purchase plan" modal posts strategy options to `POST /api/projects/{project_id}/purchase-plan`, then opens `/projects/:projectId/purchase-plans/:planId` with the returned plan in route state. The review route renders distributor-grouped plan lines, unfilled rows, TrustedParts attribution, and summary totals from the server response.

The sticky action bar keeps refresh and order conversion together. Refresh calls `POST /api/sourcing/purchase-plans/{plan_id}/refresh`; conversion calls `POST /api/sourcing/purchase-plans/{plan_id}/orders` and is disabled until the plan has been refreshed within the server's 10-minute freshness window. Manual offer override controls are visible but disabled until backend override support lands.
