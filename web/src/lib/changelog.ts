/**
 * "Latest changes" for the About page — the top of the repo's
 * `CHANGELOG.md`, copied into the bundle by `scripts/copy-docs.mjs`
 * alongside the user manual.
 *
 * The file is deliberately not machine-keyable: there is no semver, dates
 * are `YYYY-MM` only, four sections share the `2026-05` prefix, and an
 * undated `## Breaking changes` block sits above the newest dated one. So
 * this does the only honest thing available — it splits on `^## ` and
 * keeps FILE ORDER, which is the order a human maintains the file in.
 * No sorting, no date parsing, no invented version numbers.
 *
 * Bounded on purpose: the file is 422 lines and reaches back to "Phase
 * 1–3 (initial commit)". Rendering all of it would bury the part anyone
 * opening an About page actually wants.
 */
import raw from "../generated/CHANGELOG.md?raw";

export type ChangelogSection = {
  /** The `## ` heading text, e.g. "2026-09 — KiCad libraries and the agent API". */
  heading: string;
  /** Markdown body beneath the heading, up to the next `## `. */
  body: string;
};

/** How many top sections the About page shows. */
export const CHANGELOG_SECTION_LIMIT = 3;

export function parseChangelog(source: string, limit = CHANGELOG_SECTION_LIMIT): ChangelogSection[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const sections: ChangelogSection[] = [];
  let current: { heading: string; body: string[] } | null = null;

  for (const line of lines) {
    const match = /^##\s+(.*)$/.exec(line);
    if (match) {
      if (current) sections.push({ heading: current.heading, body: current.body.join("\n").trim() });
      if (sections.length >= limit) return sections;
      current = { heading: match[1].trim(), body: [] };
      continue;
    }
    if (current) current.body.push(line);
  }
  if (current && sections.length < limit) {
    sections.push({ heading: current.heading, body: current.body.join("\n").trim() });
  }
  return sections;
}

export function latestChanges(limit = CHANGELOG_SECTION_LIMIT): ChangelogSection[] {
  return parseChangelog(raw, limit);
}
