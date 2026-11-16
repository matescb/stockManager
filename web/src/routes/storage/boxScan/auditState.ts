/**
 * Pure state for the box audit page: which recorded bags were scanned
 * (and their counted quantity), what to do with the ones that were not,
 * and the audit lines sent to `POST /storage/{id}/audit` at the end.
 */
import { bucketKey, type AuditLine, type BoxBag } from "./api";

export type MissingAction = { action: "write_off" | "move" | "keep"; to?: string };

export type AuditDraft = {
  /** bucketKey → counted quantity */
  counted: Record<string, number>;
  /** bucketKey → verdict for a bag that was not scanned (default write off) */
  missing: Record<string, MissingAction>;
};

export const EMPTY_DRAFT: AuditDraft = { counted: {}, missing: {} };
export const DEFAULT_MISSING: MissingAction = { action: "write_off" };

export function findBySignature(bags: BoxBag[], sig: string | null): BoxBag | undefined {
  return sig ? bags.find(b => b.bag_signatures.includes(sig)) : undefined;
}

export function splitBags(bags: BoxBag[], draft: AuditDraft): { scanned: BoxBag[]; unscanned: BoxBag[] } {
  const scanned: BoxBag[] = [];
  const unscanned: BoxBag[] = [];
  for (const b of bags) (bucketKey(b) in draft.counted ? scanned : unscanned).push(b);
  return { scanned, unscanned };
}

export function auditLines(bags: BoxBag[], draft: AuditDraft): AuditLine[] {
  const lines: AuditLine[] = [];
  for (const b of bags) {
    if (!b.part) continue;
    const k = bucketKey(b);
    const base = { part_id: b.part.id, lot_id: b.lot?.id ?? null };
    if (k in draft.counted) {
      if (draft.counted[k] !== b.quantity) lines.push({ ...base, action: "count", actual_quantity: draft.counted[k] });
      continue;
    }
    const m = draft.missing[k] ?? DEFAULT_MISSING;
    if (m.action === "write_off") lines.push({ ...base, action: "write_off" });
    else if (m.action === "move" && m.to) lines.push({ ...base, action: "move", to_storage_location_id: m.to });
  }
  return lines;
}

export function summarize(bags: BoxBag[], draft: AuditDraft) {
  const lines = auditLines(bags, draft);
  return {
    corrected: lines.filter(l => l.action === "count").length,
    writeOff: lines.filter(l => l.action === "write_off").length,
    move: lines.filter(l => l.action === "move").length,
    lines,
  };
}

const VERSION = 1;
const key = (boxId: string) => `boxAudit:draft:${boxId}`;

export function loadAuditDraft(boxId: string): AuditDraft {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(key(boxId)) ?? "null");
    if (!parsed || parsed.v !== VERSION) return EMPTY_DRAFT;
    return { counted: parsed.counted ?? {}, missing: parsed.missing ?? {} };
  } catch {
    return EMPTY_DRAFT;
  }
}

export function saveAuditDraft(boxId: string, draft: AuditDraft): void {
  try {
    if (Object.keys(draft.counted).length === 0 && Object.keys(draft.missing).length === 0) {
      sessionStorage.removeItem(key(boxId));
    } else {
      sessionStorage.setItem(key(boxId), JSON.stringify({ v: VERSION, ...draft }));
    }
  } catch {
    /* the draft is a convenience only */
  }
}
