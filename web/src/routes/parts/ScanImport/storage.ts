/**
 * sessionStorage helpers for ScanImport draft queue persistence (FE2-018 / #54).
 *
 * Draft is keyed per-workspace so switching workspaces never leaks rows
 * across different part libraries. sessionStorage is per-tab, so the draft
 * is automatically cleared when the tab closes — no cross-session leakage.
 *
 * The persisted envelope is versioned (`{v: 1, rows: [...]}`).  If a future
 * Row shape change would crash on rehydrate we detect the mismatch via the
 * version field and silently discard the stale draft rather than exploding.
 *
 * Any row whose `state.kind === "pending"` at save-time was interrupted
 * mid-lookup.  On rehydrate those rows are coerced to
 * `{ kind: "error", message: "interrupted, re-scan to retry" }` so the
 * operator knows they need to re-scan those bags.
 */

import type { Row, LookupState } from "./types";

// Bump this when the persisted Row shape changes in a breaking way.
const CURRENT_VERSION = 1;
const INTERRUPTED_STATE: LookupState = {
  kind: "error",
  message: "interrupted, re-scan to retry",
};

type DraftEnvelope = {
  v: number;
  rows: Row[];
};

function storageKey(wsId: string): string {
  return `scanImport:draft:${wsId}`;
}

/**
 * Persist `rows` to sessionStorage for `wsId`.
 * Rows with `state.kind === "pending"` are saved as-is; they will be
 * coerced to "error" on the next loadDraft call (i.e. after a reload).
 */
export function saveDraft(wsId: string, rows: Row[]): void {
  if (!wsId) return;
  try {
    const envelope: DraftEnvelope = { v: CURRENT_VERSION, rows };
    sessionStorage.setItem(storageKey(wsId), JSON.stringify(envelope));
  } catch {
    // sessionStorage can throw in private browsing when storage is full.
    // Silently swallow — losing the draft is better than crashing the UI.
  }
}

/**
 * Load the draft for `wsId` from sessionStorage.
 *
 * Returns `null` when there is no draft, the version doesn't match, or the
 * stored JSON is corrupt.  Any `state.kind === "pending"` rows are coerced
 * to `{ kind: "error", message: "interrupted, re-scan to retry" }`.
 */
export function loadDraft(wsId: string): Row[] | null {
  if (!wsId) return null;
  try {
    const raw = sessionStorage.getItem(storageKey(wsId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      (parsed as DraftEnvelope).v !== CURRENT_VERSION ||
      !Array.isArray((parsed as DraftEnvelope).rows)
    ) {
      // Version mismatch or malformed — discard.
      clearDraft(wsId);
      return null;
    }
    const envelope = parsed as DraftEnvelope;
    const rows: Row[] = envelope.rows.map(r => ({
      ...r,
      state: r.state.kind === "pending" ? INTERRUPTED_STATE : r.state,
    }));
    return rows.length > 0 ? rows : null;
  } catch {
    // JSON.parse failure — discard silently.
    clearDraft(wsId);
    return null;
  }
}

/**
 * Remove the draft for `wsId` from sessionStorage (called after a
 * successful submitAll so the persisted queue doesn't grow stale).
 */
export function clearDraft(wsId: string): void {
  if (!wsId) return;
  try {
    sessionStorage.removeItem(storageKey(wsId));
  } catch {
    // ignore
  }
}
