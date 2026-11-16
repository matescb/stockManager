/** Types + calls for the box intake / audit endpoints (phase 15,
 *  backend `api/routes/storage_scan.py`). */
import { api } from "@/lib/api";

export type PartSummary = {
  id: string;
  name: string;
  mpn: string | null;
  manufacturer: string | null;
  description: string | null;
};

export type BagLocation = { storage_location_id: string | null; name: string | null; quantity: number };

export type ResolveResult = {
  status: "bag_here" | "bag_elsewhere" | "existing_part" | "provider_match" | "not_found";
  part: PartSummary | null;
  bag: {
    lot_id: string;
    lot_name: string | null;
    here: boolean;
    quantity_here: number;
    locations: BagLocation[];
  } | null;
  provider: { mpn: string; manufacturer: string | null; description: string | null } | null;
};

export type IntakeBody = {
  part_id?: string;
  mpn?: string;
  quantity: number;
  bag_signature?: string;
  raw_bag_code?: string;
  lot_name?: string;
  lot_serial?: string;
  comments?: string;
  on_existing?: "move" | "add_new";
};

export type IntakeResult = {
  status: "added" | "moved" | "already_here";
  part: PartSummary;
  part_created: boolean;
  lot_id: string;
  quantity: number;
  moved_from: { storage_location_id: string | null; quantity: number }[];
};

export type BoxBag = {
  part: PartSummary | null;
  lot: { id: string; name: string | null } | null;
  quantity: number;
  bag_signatures: string[];
};

export type AuditLine = {
  part_id: string;
  lot_id: string | null;
  action: "count" | "write_off" | "move";
  actual_quantity?: number;
  to_storage_location_id?: string;
};

export type AuditResult = { count: number; write_off: number; move: number; unchanged: number };

export const boxScanApi = {
  resolve: (boxId: string, body: { mpn?: string; bag_signature?: string; raw_bag_code?: string; provider_lookup?: boolean }) =>
    api.post<ResolveResult>(`/storage/${boxId}/scan/resolve`, body),
  intake: (boxId: string, body: IntakeBody) => api.post<IntakeResult>(`/storage/${boxId}/intake`, body),
  bags: (boxId: string, signal?: AbortSignal) => api.get<BoxBag[]>(`/storage/${boxId}/bags`, { signal }),
  audit: (boxId: string, lines: AuditLine[]) => api.post<AuditResult>(`/storage/${boxId}/audit`, { lines }),
  /** Set one bag's count in this box (used to correct a saved intake row). */
  setCount: (boxId: string, partId: string, lotId: string, actual: number) =>
    api.post("/stock/adjust", {
      part_id: partId,
      storage_location_id: boxId,
      lot_id: lotId,
      actual_quantity: actual,
      comments: "box intake",
    }),
};

/** Stable key of a (part, lot) bucket in a box. */
export function bucketKey(b: { part_id?: string; part?: PartSummary | null; lot_id?: string | null; lot?: { id: string } | null }): string {
  const lot = b.lot_id ?? b.lot?.id ?? null;
  const part = b.part_id ?? b.part?.id ?? "?";
  return lot ? `lot:${lot}` : `part:${part}`;
}
