/**
 * Part-detail tab reachability.
 *
 * A part has up to 17 sub-routes and SubNav used to render them as one flat
 * `inline-flex max-w-full overflow-x-auto` row. On any realistic viewport that
 * clipped the tail of the strip: Attachments, Activity, Settings and Other
 * were only reachable by horizontally scrolling a container with no scrollbar
 * affordance — effectively invisible.
 *
 * These tests pin the fix from both ends:
 *   1. every one of the 17 routes is still a target of the strip, at the same
 *      URL it always had (nothing was dropped when tabs were grouped), and
 *   2. the previously-clipped tabs render as real links inside a disclosure
 *      the user can open, with the strip itself down to 8 top-level slots.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import SubNav, { subNavTargets } from "@/components/SubNav";
import { partSubNavEntries } from "../PartLayout";
import type { Part } from "@/types";

function makePart(overrides: Partial<Part> = {}): Part {
  return {
    id: "p1",
    part_type: "meta",
    name: "Part One",
    manufacturer: null,
    mpn: null,
    internal_part_number: null,
    description: null,
    footprint: null,
    notes_markdown: null,
    low_stock_report_quantity: null,
    attrition_percentage: 0,
    attrition_min_quantity: 0,
    default_storage_location_id: null,
    default_storage_mandatory: false,
    serialized: false,
    published: false,
    linked_provider: "mouser",
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
    ...overrides,
  } as Part;
}

/** Every route `/parts/:partId/*` declared in App.tsx, in nav order. */
const ALL_TABS = [
  ["Part info", "/parts/p1/info"],
  ["Specs", "/parts/p1/specs"],
  ["Sourcing", "/parts/p1/sourcing"],
  ["CAD", "/parts/p1/cad"],
  ["Stock", "/parts/p1/stock"],
  ["Add stock", "/parts/p1/add"],
  ["Remove stock", "/parts/p1/remove"],
  ["Move stock", "/parts/p1/move"],
  ["History", "/parts/p1/history"],
  ["Authorized supply", "/parts/p1/authorized-supply"],
  ["Lots", "/parts/p1/lots"],
  ["Substitutes", "/parts/p1/substitutes"],
  ["Members", "/parts/p1/members"],
  ["Attachments", "/parts/p1/attachments"],
  ["Activity", "/parts/p1/activity"],
  ["Settings", "/parts/p1/settings"],
  ["Other", "/parts/p1/other"],
] as const;

/** The four tabs that used to fall off the right edge of the flat strip. */
const PREVIOUSLY_CLIPPED = ["Attachments", "Activity", "Settings", "Other"] as const;

function renderNav(part: Part, at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <SubNav items={partSubNavEntries(part)} />
    </MemoryRouter>,
  );
}

beforeEach(cleanup);

describe("part-detail tab strip", () => {
  it("still targets all 17 part routes, at their original URLs", () => {
    const targets = subNavTargets(partSubNavEntries(makePart()));

    expect(targets).toHaveLength(ALL_TABS.length);
    for (const [label, to] of ALL_TABS) {
      expect(
        targets.find(t => t.label === label),
        `"${label}" is no longer reachable from the part tab strip`,
      ).toEqual({ label, to });
    }
  });

  it("drops the conditional tabs for a part that has neither", () => {
    const plain = makePart({ part_type: "local", linked_provider: null, provider_links: [] });
    const labels = subNavTargets(partSubNavEntries(plain)).map(t => t.label);

    expect(labels).not.toContain("Sourcing");
    expect(labels).not.toContain("Members");
    expect(labels).toHaveLength(ALL_TABS.length - 2);
  });

  it("collapses 17 tabs into 8 top-level slots", () => {
    renderNav(makePart(), "/parts/p1/info");

    const nav = screen.getByRole("navigation", { name: "Section navigation" });
    expect(nav.children).toHaveLength(8);
  });

  it("renders every tab as a link with its own href", () => {
    renderNav(makePart(), "/parts/p1/info");

    for (const [label, to] of ALL_TABS) {
      expect(screen.getByRole("link", { name: label }).getAttribute("href")).toBe(to);
    }
  });

  it("puts the previously-clipped tabs behind a disclosure the user can open", async () => {
    const user = userEvent.setup();
    renderNav(makePart(), "/parts/p1/info");

    const more = screen.getByRole("group", { name: "More" }) as HTMLDetailsElement;
    expect(more.open).toBe(false);

    await user.click(within(more).getByText("More"));

    expect(more.open).toBe(true);
    for (const label of PREVIOUSLY_CLIPPED) {
      expect(within(more).getByRole("link", { name: label })).toBeDefined();
    }
  });

  it("names the group after the active child so the current tab stays visible", () => {
    renderNav(makePart(), "/parts/p1/other");

    const nav = screen.getByRole("navigation", { name: "Section navigation" });
    // The summary reads "Other", not "More" — otherwise a user four levels
    // into a group has no on-screen indication of where they are.
    expect(within(nav).getAllByText("Other")).not.toHaveLength(0);
    expect(within(nav).queryByText("More")).toBeNull();
  });
});
