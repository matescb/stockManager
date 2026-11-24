/**
 * "Waiting for a follow-up scan." A bag label can be read yet still lack
 * something the app needs — Mouser prints no manufacturer part number when
 * the order carried a customer P/N, and a label may lack a quantity. The
 * page then holds ONE pending bag, says exactly what to scan next, and
 * takes the next plain scan (a 1D barcode or typed text, no `[)>` header)
 * as the answer. The bag keeps everything its 2D label gave it — quantity,
 * order refs and the signature of that ORIGINAL label, so re-scanning the
 * 2D code later still finds the bag.
 *
 * Driven by what is missing: add a field to `MissingField`, a check to
 * `firstMissing` and a prompt + parser below, and every page that uses
 * this gets it.
 *
 * A full 2D label scanned while one is pending abandons the pending prompt
 * (the bag stays in the list with its inline entry box) and is processed
 * as a new bag — never merged into the pending one.
 */
import type { ScannedBag } from "./scanCode";

export type MissingField = "mpn" | "quantity";

export type FollowUp = {
  rowId: string;
  missing: MissingField;
  /** Which bag, e.g. "Murata · qty 100 · Mouser order 40489709 line 027". */
  context: string;
};

export function firstMissing(r: { mpn: string; quantity: number }): MissingField | null {
  if (!r.mpn.trim()) return "mpn";
  if (!(r.quantity > 0)) return "quantity";
  return null;
}

export function followUpPrompt(f: Pick<FollowUp, "missing" | "context">): string {
  const which = f.context ? ` (${f.context})` : "";
  switch (f.missing) {
    case "mpn": return `Scan the Mfr. Part No barcode on this label${which}`;
    case "quantity": return `Scan or type the quantity${which}`;
  }
}

/** Placeholder for the prompt's typing box. */
export function followUpPlaceholder(missing: MissingField): string {
  return missing === "mpn" ? "or type the MPN" : "or type the quantity";
}

export type FollowUpAnswer = { mpn: string } | { quantity: number };

/** Read a follow-up answer from a scan or typed text. null = not an
 *  answer to this question (the caller says so and keeps waiting). */
export function parseFollowUpAnswer(missing: MissingField, input: ScannedBag | string): FollowUpAnswer | null {
  const text = (typeof input === "string" ? input : input.raw).trim();
  if (!text || text.startsWith("[)>")) return null;
  if (missing === "mpn") {
    // A scanned 1D code may carry its DI ("1PGRM188…"); the parser strips it.
    const mpn = typeof input === "string" ? text : input.bag.mpn.trim();
    return mpn ? { mpn } : null;
  }
  const m = /^Q?(\d{1,7})$/i.exec(text);
  const quantity = m ? parseInt(m[1], 10) : NaN;
  return quantity > 0 ? { quantity } : null;
}
