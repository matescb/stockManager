/**
 * Shared types + small utilities for the ScanImport route.
 *
 * `web/src/routes/parts/ScanImport.tsx` is 731 lines (CQ-003 / #119)
 * and is being split into co-located sub-components (ScanImportSession,
 * ScanImportRowEditor, ScanImportSubmit). This file is the seam: the
 * orchestration root and the future sub-components all reach for the
 * same `Row`, `LookupState`, `ImportResponse`, etc. shapes.
 *
 * No behaviour change. Subsequent step PRs (extract camera + lookup
 * loop, extract row editor, extract submit panel) import from here.
 */
import type { BagCode } from "@/lib/bagCode";
import type { MpnLookupResult, Part } from "@/types";

export const PROVIDER_LABEL: Record<string, string> = {
  mouser: "Mouser",
  digikey: "DigiKey",
  none: "no provider",
};

// Module-level constant — referenced by `<Scanner symbologies={...}>`.
// Inline `["DataMatrix", "QR"]` would be a fresh array on every render,
// which (combined with effect deps in ScanditScanner / ZxingScanner)
// would tear down and rebuild the multi-MB scanner SDK on every parent
// state change. FE CRIT-2 in the 2026-04-30 review.
export const SCAN_IMPORT_SYMBOLOGIES = ["DataMatrix", "QR"] as const;

export type LookupState =
  | { kind: "pending" }
  | { kind: "duplicate"; existing: Part }
  | { kind: "found"; result: NonNullable<MpnLookupResult["result"]>; provider: string }
  // Same physical bag was imported earlier — surface the prior coordinates
  // so the user can either open the part or consume from the lot inline.
  | {
      kind: "bag_rescan";
      part_id: string;
      lot_id: string | null;
      storage_location_id: string | null;
      quantity: number;
    }
  | { kind: "consumed"; partId: string; quantity: number }
  | { kind: "error"; message: string };

export type Row = {
  rowId: string;     // local id for rendering / dedup
  bag: BagCode;      // every field the parser pulled off the bag
  bagSig: string | null;  // sha256 of the raw bag — null when no Web Crypto / empty
  quantity: number;  // editable, defaults to bag's Q if any
  state: LookupState;
};

export type ImportResultRow = {
  mpn: string;
  status: "created" | "duplicate" | "bag_rescan" | "lookup_failed" | "invalid";
  part_id?: string;
  quantity_added?: number;
  stock_error?: string | null;
  error?: string;
};

export type ImportResponse = {
  rows: ImportResultRow[];
  summary: {
    created: number;
    duplicate: number;
    bag_rescan: number;
    lookup_failed: number;
    invalid: number;
  };
  provider: string;
};

/** Loose, case-insensitive comparison so "Molex" matches "MOLEX INC."
 *  and "Yageo" matches "YAGEO Phycomp". Returns true when the two
 *  strings refer to the same manufacturer. */
export function manufacturerMatches(
  bagMfr: string | undefined,
  providerMfr: string | null | undefined,
): boolean {
  if (!bagMfr || !providerMfr) return true;  // can't disagree if one is missing
  const a = bagMfr.toLowerCase().replace(/\s+/g, " ").trim();
  const b = providerMfr.toLowerCase().replace(/\s+/g, " ").trim();
  return a.includes(b) || b.includes(a);
}

export function newRowId(): string {
  // crypto.randomUUID would also work but isn't guaranteed in older TLS hosts.
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
