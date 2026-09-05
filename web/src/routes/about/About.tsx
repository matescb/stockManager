/**
 * `/about` — which build am I looking at, and what changed recently.
 *
 * ---------------------------------------------------------------------
 * Where the version string comes from
 * ---------------------------------------------------------------------
 * The only trustworthy identifier this project has is the 12-character
 * git short SHA of the deployed commit. CI derives it once
 * (`git rev-parse --short=12 HEAD`) and it flows to both halves of the
 * stack: `VITE_APP_VERSION` is inlined into the SPA bundle at build time,
 * and `SENTRY_RELEASE` reaches the backend as an env var and comes back
 * from `GET /api/version`. It is also the Sentry release tag, so a SHA
 * pasted into a bug report maps to a deployment and to its stack traces.
 *
 * The `0.1.0` in `web/package.json`, `backend/pyproject.toml` and
 * `main.py`'s `FastAPI(version=…)` are NOT shown here: all three have been
 * frozen at the initial commit for 655 commits, and there are no git tags
 * to derive a semver from. Displaying `0.1.0` would be worse than showing
 * nothing — it looks like an answer.
 *
 * ---------------------------------------------------------------------
 * Why both builds, side by side
 * ---------------------------------------------------------------------
 * There is no staging environment, and the auto-deploy rebuilds the web
 * image and the backend image separately — so it can half-apply and leave
 * a new SPA talking to an old API (or the reverse). That is a real failure
 * mode with a confusing signature (features missing, 404s on new routes),
 * and the two SHAs disagreeing is the cheapest possible diagnosis. Hence
 * they are shown together, with an explicit banner on mismatch rather than
 * two strings a user is expected to diff by eye.
 */
import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import Markdown from "@/components/Markdown";
import { latestChanges, CHANGELOG_SECTION_LIMIT } from "@/lib/changelog";

type VersionPayload = { build: string | null };

/**
 * Not workspace-scoped, so deliberately NOT built with `useWsKey`: the
 * build id is a property of the server, identical for every tenant, and
 * a per-workspace cache entry would just refetch it on each switch.
 */
const VERSION_KEY = ["version"];

function formatBuildTime(raw: string | undefined): string | null {
  if (!raw) return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString();
}

function BuildRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="card p-3">
      <div className="section-title">{label}</div>
      <div className="mt-1 font-mono text-sm break-all">
        {value ?? <span className="text-muted font-sans">unknown — development build</span>}
      </div>
    </div>
  );
}

export default function About() {
  // Read inside render (not at module scope) so the value reflects the
  // environment the component actually runs in.
  const frontendBuild = import.meta.env.VITE_APP_VERSION || null;
  const builtAt = formatBuildTime(import.meta.env.VITE_BUILD_TIME);

  const versionQuery = useQuery({
    queryKey: VERSION_KEY,
    queryFn: ({ signal }) => api.get<VersionPayload>("/version", { signal }),
    staleTime: 5 * 60_000,
  });

  const backendBuild = versionQuery.data?.build || null;
  const mismatch = Boolean(frontendBuild && backendBuild && frontendBuild !== backendBuild);

  const sections = latestChanges();

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-xl font-semibold">About Stock Manager</h1>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">This install</h2>

        {mismatch && (
          <div
            role="alert"
            className="card p-3 border-warning/50 bg-warning/10 text-sm"
          >
            <div className="font-medium">Frontend and backend builds differ.</div>
            <div className="text-muted mt-0.5">
              A deploy may have only half-applied. Reload the page first; if the two
              stay different, quote both identifiers to your administrator.
            </div>
          </div>
        )}

        <InlineQueryError query={versionQuery} label="the backend build" />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <BuildRow label="Frontend build" value={frontendBuild} />
          <BuildRow
            label="Backend build"
            value={versionQuery.isPending ? null : backendBuild}
          />
        </div>

        <p className="text-xs text-muted">
          Each identifier is the 12-character commit the running code was built from.
          {builtAt ? ` Frontend built ${builtAt}.` : ""} Include them in any bug report —
          they are what pins a report to a specific deployment.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Latest changes</h2>
        {sections.length === 0 ? (
          <p className="text-sm text-muted">No changelog entries in this build.</p>
        ) : (
          sections.map(section => (
            <div key={section.heading} className="card p-4">
              <div className="text-base font-semibold">{section.heading}</div>
              <Markdown>{section.body}</Markdown>
            </div>
          ))
        )}
        <p className="text-xs text-muted">
          The {CHANGELOG_SECTION_LIMIT} most recent entries from the project changelog.
        </p>
      </section>

      <section>
        <Link to="/help" className="btn">
          <BookOpen size={14} />
          Open the manual
        </Link>
      </section>
    </div>
  );
}
