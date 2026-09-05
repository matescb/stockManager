/**
 * Wire types for the printable pick list (Track B4).
 *
 * Deliberately local to this folder rather than added to the shared
 * `@/types` barrel: nothing outside the pick-list view consumes them, and
 * the barrel is a high-traffic file.
 *
 * Every `quantity` field is the server's `quantity_out` rendering of a
 * `Numeric(18, 6)` column — a whole number comes back as an integer, a
 * fractional one as a float. Format them, never `Math.floor` them.
 */

export type PickListPick = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  mpn: string | null;
  designators: string[];
  lot_id: string | null;
  lot_name: string | null;
  /** How many to take from THIS location. */
  quantity: number;
  unit: string;
  /** What the location holds in total, so a full bin is obvious. */
  available: number;
};

export type PickListStop = {
  storage_location_id: string | null;
  /** Already resolved server-side; "Unassigned" for stock in no location. */
  storage_location_name: string;
  picks: PickListPick[];
};

export type PickListLine = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  mpn: string | null;
  manufacturer: string | null;
  internal_part_number: string | null;
  designators: string[];
  unit: string;
  attrition_pct: number;
  /** Only set on a per-stage sheet: this stage's share of the BOM line. */
  portion_pct: number | null;
  /** Attrition-adjusted demand from the server's `_required`. */
  required: number;
  /** The part's own total on hand. Can exceed `planned` without the line
   *  being covered: a part on two BOM lines shares one pool. */
  on_hand: number;
  /** Stock in registered substitutes / meta-part members. Reported so the
   *  sheet agrees with the build screen; never picked from. */
  alternates_available: number;
  /** Sum of the per-location picks — `required` unless the line is short. */
  planned: number;
  short_by: number;
  is_short: boolean;
  /** DISTINCT locations, not picks: two lots on one shelf are two picks
   *  but one stop, so this matches the "Locations" column header. */
  location_count: number;
};

export type PickListStage = {
  id: string;
  name: string;
  sequence: number;
  status: string;
};

export type PickList = {
  build: { id: string; name: string; quantity: number; status: string };
  project: { id: string; name: string };
  stage: PickListStage | null;
  generated_at: string;
  lines: PickListLine[];
  stops: PickListStop[];
  totals: { lines: number; short_lines: number; stops: number };
};
