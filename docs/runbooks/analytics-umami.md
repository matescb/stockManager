# Umami analytics

Audience: engineer / on-call

Self-hosted [Umami](https://umami.is) running at `https://stats.matescb.cz`,
tracking pageviews + SPA route changes for `parts.matescb.cz`. No cookies,
respects `Do-Not-Track`, no PII collected.

## How it's wired

The tracker is a single client-side script injected by a small loader IIFE
in [`web/index.html`](../../web/index.html). The IIFE only injects the
script tag when **both** of these env vars are non-empty at build time:

| Env var | Purpose | Example |
|---|---|---|
| `VITE_UMAMI_WEBSITE_ID` | Umami's per-site UUID | `5fdea605-7472-4437-8035-f8602d5bd627` |
| `VITE_UMAMI_SCRIPT_URL` | Tracking script URL | `https://stats.matescb.cz/script.js` |

Both are baked into the SPA bundle by Vite at build time
(`web/Dockerfile.prod` ARG → ENV → Vite `%VITE_…%` substitution in
`index.html`). They are **not secrets** — both values are visible in
DevTools to anyone who loads the page. Umami treats the website ID as an
identifier, not an auth token, the same way Sentry treats the public DSN.

Empty values are first-class: dev builds, CI builds, and any prod deploy
that omits these env vars produce a bundle that injects nothing — no
script tag, no network call, no DOM mutation. The IIFE also bails when
the placeholder string `%VITE_…` survives un-substituted (the dev server
serves `index.html` without transformation).

## Disable tracking

```bash
# In .env.prod on the VPS:
VITE_UMAMI_WEBSITE_ID=
VITE_UMAMI_SCRIPT_URL=
```

Then redeploy (or wait for the next auto-deploy on `main`). The next
build inlines empty values, the IIFE no-ops, and no requests go to
`stats.matescb.cz` from the SPA.

## Rotate the website ID

When: someone leaks the UUID publicly and you want a clean cut on which
historical pageviews are theirs vs. yours, OR you migrate Umami to a new
host.

1. **Umami UI** → Settings → Websites → click the site → **Reset** (or
   create a new site for the same domain). Copy the new UUID.
2. **`.env.prod` on the VPS** → set `VITE_UMAMI_WEBSITE_ID=<new-uuid>`.
3. **Redeploy** — `git push` to `main` triggers the auto-deploy, or
   manually:

   ```bash
   ssh deploy@<vps-host>
   cd /opt/stockmanager        # TODO(verify): exact path on VPS
   docker compose -f docker-compose.prod.yml up -d --build web
   ```

4. **Verify** — load `https://parts.matescb.cz`, open DevTools → Network,
   confirm a `script.js` request to `stats.matescb.cz`. Then check the
   new site in Umami within ~10s for the first pageview.

The old site keeps any historical pageviews. New pageviews go to the new
ID. Plan for split data in any historical reports that span the cutover.

## Debug a missing pageview

Symptom: production site loads fine but Umami shows no new pageviews.

1. **Browser DevTools → Network** on `parts.matescb.cz`. Filter for
   `script.js`.
   - **No request at all** → tracker not injected. Likely cause: build
     env vars empty. Check `https://parts.matescb.cz/index.html` view
     source — the loader IIFE should be present, and it should reference
     the real UUID (not a `%VITE_…%` placeholder).
   - **Request returns 404 / wrong URL** → `VITE_UMAMI_SCRIPT_URL` is
     misconfigured.
   - **Request returns 200 but no pageview pings** → the script loaded
     but the website ID doesn't match a site in Umami. Check the
     `data-website-id` attribute on the injected `<script>` tag against
     the UUID in Umami → Settings → Websites.

2. **Do-Not-Track**: Umami's official script honours the browser's DNT
   header by design. If the missing pageview is yours, check
   browser/extension DNT settings.

3. **Ad-blockers**: many block `*/script.js` heuristically. Disable in
   the browser, retry. (This is intentional on Umami's side — using a
   common script name avoids singling out trackers, but blockers still
   catch it. Acceptable trade-off for self-hosted analytics.)

4. **Umami server**: check `https://stats.matescb.cz` is reachable; if
   not, escalate to whoever runs the analytics VPS (separate from the
   stockManager VPS — different incident).

## Privacy posture

- **No cookies set** by the Umami client script (Umami uses
  fingerprint-free, server-side aggregation by IP+UA hash, rotated
  daily).
- **DNT respected** by the official `script.js`.
- **No PII** collected — Umami records URL path, referrer, viewport,
  language, country (from IP, then IP discarded), and event payloads
  the app explicitly sends. The stockManager SPA does not call Umami's
  custom-event API today, so the only data is pageview pings.
- The privacy posture is documented in [`docs/user/privacy.md`]
  (../user/privacy.md) for end users — keep both pages in sync if you
  change what's collected.

## Related

- [`docs/phases/12-observability-sentry.md`](../phases/12-observability-sentry.md)
  — Sentry (errors) is the other observability system; Umami is for
  product/usage signal.
- [`docs/runbooks/sentry-triage.md`](sentry-triage.md) — sister runbook
  for the error side.
- [`docs/adr/0019-umami-self-hosted-analytics.md`](../adr/0019-umami-self-hosted-analytics.md)
  — why self-hosted Umami over Plausible / GA4 / no analytics.
