/** Classify what a scanner (camera, wedge or paste) produced. */
import { bagComments, bagLotName, bagSignature, parseBagCode, type BagCode } from "@/lib/bagCode";

export type ScannedBag = {
  kind: "bag";
  raw: string;
  bag: BagCode;
  sig: string | null;
  lotName: string | null;
  comments: string | null;
};

export type Scanned = ScannedBag | { kind: "box_label"; code: string } | { kind: "unreadable"; raw: string };

// A printed label's QR encodes `<APP_BASE_URL>/c/<code>`.
const LABEL_URL = /\/c\/([A-Za-z0-9_-]+)\/?$/;

export async function classifyScan(raw: string): Promise<Scanned> {
  const text = raw.trim();
  const label = LABEL_URL.exec(text);
  if (label && /^https?:\/\//i.test(text)) return { kind: "box_label", code: label[1] };
  const bag = parseBagCode(raw);
  // A no-MPN bag label is still a bag: its signature can match a recorded
  // bag, and its quantity / order refs are kept while the MPN is typed.
  if (!bag.mpn.trim() && !bag.noMpn) return { kind: "unreadable", raw };
  return {
    kind: "bag",
    raw,
    bag,
    sig: await bagSignature(raw),
    lotName: bagLotName(bag),
    comments: bagComments(bag),
  };
}
