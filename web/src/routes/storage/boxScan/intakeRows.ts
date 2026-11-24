/** Row model + per-box session draft for the intake page. */
import type { IntakeResult, ResolveResult } from "./api";

export type IntakePhase = "resolving" | "needs_mpn" | "needs_choice" | "ready" | "saving" | "saved" | "error";

export type IntakeRow = {
  id: string;
  mpn: string;
  quantity: number;
  /** Count last written to the server; differs from `quantity` after an edit. */
  savedQuantity?: number;
  sig?: string | null;
  raw?: string;
  lotName?: string | null;
  lotSerial?: string | null;
  comments?: string | null;
  /** Set when the scanned label had no 1P — the message to show while
   *  the operator supplies the MPN. */
  noMpnMessage?: string;
  /** Which bag, for a follow-up prompt ("Murata · qty 100 · Mouser order …"). */
  context?: string;
  phase: IntakePhase;
  resolve?: ResolveResult;
  result?: Pick<IntakeResult, "status" | "lot_id" | "part" | "part_created">;
  error?: string;
};

let seq = 0;
export function newRowId(): string {
  seq += 1;
  return `${Date.now().toString(36)}-${seq}`;
}

const VERSION = 1;
const key = (boxId: string) => `boxIntake:draft:${boxId}`;

export function loadIntakeDraft(boxId: string): IntakeRow[] {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(key(boxId)) ?? "null");
    if (!parsed || parsed.v !== VERSION || !Array.isArray(parsed.rows)) return [];
    return (parsed.rows as IntakeRow[]).map(r =>
      r.phase === "resolving" || r.phase === "saving"
        ? { ...r, phase: "error", error: "Interrupted — scan the bag again" }
        : r,
    );
  } catch {
    return [];
  }
}

export function saveIntakeDraft(boxId: string, rows: IntakeRow[]): void {
  try {
    if (rows.length === 0) sessionStorage.removeItem(key(boxId));
    else sessionStorage.setItem(key(boxId), JSON.stringify({ v: VERSION, rows }));
  } catch {
    /* quota / private mode: the draft is a convenience only */
  }
}
