/**
 * The in-app manual: loading, parsing and link policy for the end-user
 * documentation shelf.
 *
 * Source of truth is `docs/user/*.md` in the repo. `scripts/copy-docs.mjs`
 * (predev / prebuild / pretest) copies the shelf into `src/generated/user-docs/`
 * and Vite inlines it here with `import.meta.glob(..., { query: "?raw" })`,
 * so the manual ships inside the SPA bundle with no backend route and no
 * runtime fetch. See the script header for why a served endpoint isn't
 * viable (docs/ is outside the backend build context; nothing mounts
 * StaticFiles).
 *
 * ---------------------------------------------------------------------
 * The doc-shelf boundary (CLAUDE.md)
 * ---------------------------------------------------------------------
 * `docs/` has two audiences. `docs/user/` is the END-USER shelf; every
 * other page under `docs/` (ARCHITECTURE, api/, adr/, runbooks/, phases/)
 * is engineer-only. The in-app manual surfaces the user shelf and nothing
 * else — so `resolveDocHref` blocks every relative link that leaves the
 * shelf rather than rendering a link that would 404 in the SPA and would
 * point a warehouse operator at an ADR if it didn't. `docs/user/README.md:5`
 * links up to `../README.md` (the engineer index); that is correct on
 * GitHub and blocked here. `lib/__tests__/userDocs.test.ts` pins it across
 * every shipped page.
 */
import { isSafeHttpOrSameOriginUrl } from "./url";

// Raw markdown, keyed by the generated path. `eager` because the manual is
// ~1176 lines of text total — smaller than a single icon chunk — and a
// lazy glob would add a Suspense boundary per help page for no benefit.
const RAW_PAGES = import.meta.glob("../generated/user-docs/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** `docs/user/README.md` is the shelf index; it renders at `/help`, not `/help/README`. */
const INDEX_FILE = "README";

export const HELP_BASE = "/help";

export type UserDoc = {
  /** URL slug, e.g. "scan-import". The index has slug "". */
  slug: string;
  /** H1 text from line 1 of the source file. */
  title: string;
  /** Markdown body with the H1, the `Audience:` line and screenshot placeholders removed. */
  body: string;
};

function fileStem(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/, "");
}

/**
 * Strip the three things every `docs/user/` page carries that would look
 * wrong rendered in-app:
 *
 *  1. the H1 on line 1 — the route draws the page title in the app's own
 *     heading style, so leaving it in prints the title twice;
 *  2. the `Audience: end user` line on line 3 — a docs-shelf convention
 *     for engineers browsing the repo, meaningless to the reader who is
 *     already in the app;
 *  3. the 29 `> _Screenshot: …_` placeholders — rendered as-is they show
 *     up as italic blockquotes reading "Screenshot: the parts list with
 *     the search box focused", which makes a shipped product look
 *     unfinished. They come back for free once real screenshots land.
 */
export function parseDoc(raw: string): { title: string; body: string } {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");

  let title = "";
  const kept: string[] = [];
  for (const line of lines) {
    if (!title && /^#\s+/.test(line)) {
      title = line.replace(/^#\s+/, "").trim();
      continue;
    }
    if (/^Audience:\s/i.test(line)) continue;
    if (/^>\s*_Screenshot:/i.test(line)) continue;
    kept.push(line);
  }

  return { title, body: kept.join("\n").replace(/^\n+/, "").replace(/\n{3,}/g, "\n\n") };
}

function buildDocs(): UserDoc[] {
  return Object.entries(RAW_PAGES)
    .map(([path, raw]) => {
      const stem = fileStem(path);
      const { title, body } = parseDoc(raw);
      return { slug: stem === INDEX_FILE ? "" : stem, title, body };
    })
    .sort((a, b) => a.slug.localeCompare(b.slug));
}

const DOCS = buildDocs();
const BY_SLUG = new Map(DOCS.map(d => [d.slug, d]));

/**
 * Reading order, taken from the order `docs/user/README.md` links its
 * siblings — a curated learning path (getting-started → day-to-day →
 * settings), not alphabetical. Pages the index doesn't link fall to the
 * end in slug order, so a new file is never silently invisible.
 */
function indexOrder(): string[] {
  const index = BY_SLUG.get("");
  if (!index) return [];
  const out: string[] = [];
  for (const href of extractHrefs(index.body)) {
    const stem = href.split("#", 1)[0];
    if (stem.includes("/") || !stem.endsWith(".md")) continue;
    const slug = stem.slice(0, -3);
    if (slug !== INDEX_FILE && !out.includes(slug)) out.push(slug);
  }
  return out;
}

/** The shelf index page (`docs/user/README.md`). */
export function getIndexDoc(): UserDoc | undefined {
  return BY_SLUG.get("");
}

/** Every page except the index, in the index's reading order. */
export function listDocs(): UserDoc[] {
  const order = indexOrder();
  const rank = (slug: string) => {
    const i = order.indexOf(slug);
    return i === -1 ? order.length : i;
  };
  return DOCS.filter(d => d.slug !== "").sort(
    (a, b) => rank(a.slug) - rank(b.slug) || a.slug.localeCompare(b.slug),
  );
}

export function getDoc(slug: string): UserDoc | undefined {
  return BY_SLUG.get(slug);
}

export function hasDoc(slug: string): boolean {
  return BY_SLUG.has(slug);
}

// ---------------------------------------------------------------------
// Link policy
// ---------------------------------------------------------------------

export type DocLink =
  /** An in-app route — render with react-router's <Link>. */
  | { kind: "internal"; to: string }
  /** A same-page fragment — render as a plain <a href="#…">. */
  | { kind: "anchor"; href: string }
  /** A vetted absolute http(s) URL — render as <a target="_blank">. */
  | { kind: "external"; href: string }
  /** Not renderable as a link here; the label is shown as plain text. */
  | { kind: "blocked"; reason: string };

/**
 * Map a markdown href from a `docs/user/` page onto something the SPA can
 * actually navigate to.
 *
 * The shelf uses bare sibling filenames (`stock.md`, and one
 * `parts.md#pick-a-part-type`), which resolve correctly on GitHub and 404
 * in the app. Anything carrying a path separator has left the shelf —
 * `../README.md` (engineer index), `docs/adr/0029-….md` (an ADR linked
 * from CHANGELOG.md) — and is blocked by the doc-shelf boundary above.
 */
export function resolveDocHref(href: string | null | undefined): DocLink {
  const raw = (href ?? "").trim();
  if (!raw) return { kind: "blocked", reason: "empty href" };

  if (raw.startsWith("#")) return { kind: "anchor", href: raw };

  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) {
    // Absolute URL of some scheme. Only http(s) (or same-origin) survives;
    // `isSafeHttpOrSameOriginUrl` is the same guard the app applies to
    // every user-supplied link elsewhere, so `javascript:` and friends die
    // here rather than in a component.
    return isSafeHttpOrSameOriginUrl(raw)
      ? { kind: "external", href: raw }
      : { kind: "blocked", reason: "unsupported URL scheme" };
  }

  const [pathPart, fragment = ""] = raw.split("#", 2);
  if (!pathPart) return { kind: "blocked", reason: "empty path" };

  if (pathPart.includes("/")) {
    return { kind: "blocked", reason: "outside the end-user doc shelf" };
  }
  if (!pathPart.endsWith(".md")) {
    return { kind: "blocked", reason: "not a markdown page" };
  }

  const stem = pathPart.slice(0, -3);
  const slug = stem === INDEX_FILE ? "" : stem;
  if (!BY_SLUG.has(slug)) {
    return { kind: "blocked", reason: "no such help page" };
  }

  const suffix = fragment ? `#${fragment}` : "";
  return { kind: "internal", to: slug ? `${HELP_BASE}/${slug}${suffix}` : `${HELP_BASE}${suffix}` };
}

/** Every markdown link target found in `body`, for boundary tests. */
export function extractHrefs(body: string): string[] {
  const out: string[] = [];
  const re = /\[[^\]]*\]\(([^)\s]+)[^)]*\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) out.push(m[1]);
  return out;
}
