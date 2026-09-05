/**
 * Link policy and parsing for the in-app manual.
 *
 * The load-bearing test here is the last one: it walks every shipped
 * `docs/user/` page and asserts that nothing which resolves to a link
 * escapes the end-user shelf. `CLAUDE.md` states the boundary ("Engineer-only
 * links should never point here, and vice versa"), and `docs/user/README.md`
 * legitimately links up to the engineer index on GitHub — so the rule has to
 * be enforced at render time, not by asking authors to remember.
 */
import { describe, it, expect } from "vitest";
import {
  parseDoc,
  resolveDocHref,
  listDocs,
  getDoc,
  getIndexDoc,
  extractHrefs,
} from "../userDocs";
import { parseChangelog } from "../changelog";

describe("parseDoc", () => {
  it("lifts the H1 out of the body and returns it as the title", () => {
    const { title, body } = parseDoc("# Add parts\n\nAudience: end user\n\nSome prose.\n");
    expect(title).toBe("Add parts");
    expect(body).not.toContain("# Add parts");
    expect(body).toContain("Some prose.");
  });

  it("drops the docs-shelf `Audience:` convention line", () => {
    const { body } = parseDoc("# T\n\nAudience: end user\n\nBody.\n");
    expect(body).not.toMatch(/Audience:/);
  });

  it("drops `> _Screenshot: …_` placeholders so the manual doesn't look unfinished", () => {
    const { body } = parseDoc(
      "# T\n\nIntro.\n\n> _Screenshot: the parts list with the search box focused_\n\nOutro.\n",
    );
    expect(body).not.toMatch(/Screenshot/);
    expect(body).toContain("Intro.");
    expect(body).toContain("Outro.");
  });
});

describe("resolveDocHref", () => {
  it("rewrites a sibling .md link to an in-app help route", () => {
    expect(resolveDocHref("stock.md")).toEqual({ kind: "internal", to: "/help/stock" });
  });

  it("preserves the fragment on the one anchored cross-page link", () => {
    // projects-and-bom.md:115 → parts.md#pick-a-part-type
    expect(resolveDocHref("parts.md#pick-a-part-type")).toEqual({
      kind: "internal",
      to: "/help/parts#pick-a-part-type",
    });
  });

  it("maps the shelf index onto /help", () => {
    expect(resolveDocHref("README.md")).toEqual({ kind: "internal", to: "/help" });
  });

  it("keeps a same-page anchor as an anchor", () => {
    expect(resolveDocHref("#pick-a-part-type")).toEqual({
      kind: "anchor",
      href: "#pick-a-part-type",
    });
  });

  it("blocks the upward link to the engineer docs index", () => {
    // docs/user/README.md:5 → ../README.md
    expect(resolveDocHref("../README.md").kind).toBe("blocked");
  });

  it("blocks engineer-shelf paths of every flavour", () => {
    for (const href of [
      "docs/adr/0029-api-tokens-and-csrf-exemption.md",
      "docs/phases/14-kicad-and-agent-api.md",
      "docs/api/agents.md",
      "docs/runbooks/sentry-triage.md",
      "../ARCHITECTURE.md",
    ]) {
      expect(resolveDocHref(href).kind, href).toBe("blocked");
    }
  });

  it("blocks a .md target that isn't a page in this build", () => {
    expect(resolveDocHref("privacy.md").kind).toBe("blocked");
  });

  it("blocks dangerous URL schemes", () => {
    // eslint-disable-next-line no-script-url
    expect(resolveDocHref("javascript:alert(1)").kind).toBe("blocked");
    expect(resolveDocHref("data:text/html,<script>").kind).toBe("blocked");
  });

  it("allows a vetted absolute http(s) URL", () => {
    expect(resolveDocHref("https://example.com/x")).toEqual({
      kind: "external",
      href: "https://example.com/x",
    });
  });
});

describe("the bundled shelf", () => {
  it("ships every docs/user page", () => {
    const slugs = listDocs().map(d => d.slug);
    expect(slugs).toContain("getting-started");
    expect(slugs).toContain("parts");
    expect(slugs).toContain("kicad");
    // 14 files in docs/user/, one of which is the index.
    expect(slugs.length).toBe(13);
  });

  it("orders pages the way the index lists them, not alphabetically", () => {
    expect(listDocs()[0].slug).toBe("getting-started");
  });

  it("gives every page a title", () => {
    for (const doc of [getIndexDoc()!, ...listDocs()]) {
      expect(doc.title, doc.slug).toBeTruthy();
    }
  });

  it("resolves the parts page by slug", () => {
    expect(getDoc("parts")?.title).toBeTruthy();
  });

  it("never renders a link that leaves the end-user shelf", () => {
    const offenders: string[] = [];
    for (const doc of [getIndexDoc()!, ...listDocs()]) {
      for (const href of extractHrefs(doc.body)) {
        const link = resolveDocHref(href);
        if (link.kind === "internal" && !link.to.startsWith("/help")) {
          offenders.push(`${doc.slug || "README"} → ${href}`);
        }
        if (link.kind === "external") {
          offenders.push(`${doc.slug || "README"} → ${href} (external)`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("resolves every in-shelf link to a page that exists", () => {
    const dangling: string[] = [];
    for (const doc of [getIndexDoc()!, ...listDocs()]) {
      for (const href of extractHrefs(doc.body)) {
        if (href.includes("/") || !href.endsWith(".md")) continue;
        if (resolveDocHref(href).kind !== "internal") {
          dangling.push(`${doc.slug || "README"} → ${href}`);
        }
      }
    }
    expect(dangling).toEqual([]);
  });
});

describe("parseChangelog", () => {
  const sample = [
    "# Changelog",
    "",
    "Preamble that belongs to no section.",
    "",
    "## Breaking changes",
    "",
    "- something broke",
    "",
    "## 2026-09 — a release",
    "",
    "- a change",
    "",
    "## 2026-05 — older",
    "",
    "- older change",
    "",
    "## 2026-04 — oldest",
    "",
    "- oldest change",
  ].join("\n");

  it("splits on `## ` and keeps file order (no date parsing, no sorting)", () => {
    const sections = parseChangelog(sample, 3);
    expect(sections.map(s => s.heading)).toEqual([
      "Breaking changes",
      "2026-09 — a release",
      "2026-05 — older",
    ]);
  });

  it("bounds the number of sections", () => {
    expect(parseChangelog(sample, 1)).toHaveLength(1);
    expect(parseChangelog(sample, 99)).toHaveLength(4);
  });

  it("captures the section body and drops the preamble", () => {
    const [first] = parseChangelog(sample, 1);
    expect(first.body).toBe("- something broke");
  });
});
