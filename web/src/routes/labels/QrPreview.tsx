/**
 * QR placeholder for the canvas — dimensionally exact, visually indicative.
 *
 * Adapted from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/QrPreview.tsx`), which
 * renders a real symbol with the `qrcode` npm package. We deliberately do NOT
 * take that dependency:
 *
 *  - **Nothing here is ever printed.** The cab SQUIX generates the symbol
 *    itself from the JScript `B` command (`label_render._add_element`), so a
 *    client-side encoder would only ever produce a picture of a QR, never the
 *    bytes that get burned. Shipping an encoder for that is a supply-chain
 *    cost with no print-path benefit.
 *  - **What the designer actually needs is the FOOTPRINT**, and that we can
 *    compute exactly: `geometry.qrModuleCount` derives the QR version from the
 *    payload's byte length and the EC level using the ISO 18004 capacity
 *    table, and the printer picks the same version. So the box on screen is
 *    the real printed square, to the module.
 *
 * The module pattern inside the box is therefore illustrative: real finder,
 * separator, timing and alignment patterns (so it reads as a QR and its
 * quiet-zone-free extent is honest) with deterministic pseudo-random data
 * modules. It is not scannable, and the JScript panel in the editor shows the
 * true payload the printer will encode.
 */
import { useMemo } from "react";
import { mmToPx, qrModuleCount } from "./geometry";
import type { QrEcLevel } from "./types";

interface QrPreviewProps {
  /** The resolved payload (already binding-resolved and sanitised). */
  value: string;
  /** Module ("dot") size in mm — the `dotsize_mm` element field. */
  dotsizeMm: number;
  ec: QrEcLevel;
  /** Canvas zoom, px per mm. */
  pxPerMm: number;
}

/** FNV-1a. Deterministic so the same payload always draws the same pattern. */
function hash32(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Is (row, col) inside one of the three 7x7 finder patterns? */
function finderModule(row: number, col: number, n: number): boolean | null {
  const corners: Array<[number, number]> = [
    [0, 0],
    [0, n - 7],
    [n - 7, 0],
  ];
  for (const [r0, c0] of corners) {
    const r = row - r0;
    const c = col - c0;
    if (r < -1 || r > 7 || c < -1 || c > 7) continue;
    // The 1-module separator ring around each finder is always light.
    if (r < 0 || r > 6 || c < 0 || c > 6) return false;
    const ring = r === 0 || r === 6 || c === 0 || c === 6;
    const core = r >= 2 && r <= 4 && c >= 2 && c <= 4;
    return ring || core;
  }
  return null;
}

/** The single 5x5 alignment pattern present from version 2 upward. */
function alignmentModule(row: number, col: number, n: number): boolean | null {
  if (n < 25) return null; // version 1 has no alignment pattern
  const centre = n - 7;
  const r = row - centre;
  const c = col - centre;
  if (Math.abs(r) > 2 || Math.abs(c) > 2) return null;
  const ring = Math.abs(r) === 2 || Math.abs(c) === 2;
  return ring || (r === 0 && c === 0);
}

/** Build the SVG path for every dark module, as one `d` string. */
function darkModulePath(payload: string, n: number): string {
  const seed = hash32(payload);
  const parts: string[] = [];
  for (let row = 0; row < n; row += 1) {
    for (let col = 0; col < n; col += 1) {
      const finder = finderModule(row, col, n);
      const alignment = finder === null ? alignmentModule(row, col, n) : null;
      let dark: boolean;
      if (finder !== null) {
        dark = finder;
      } else if (alignment !== null) {
        dark = alignment;
      } else if (row === 6 || col === 6) {
        dark = (row + col) % 2 === 0; // timing patterns
      } else {
        // Deterministic fill at roughly the ~48% density a real symbol has.
        const mixed = Math.imul(seed ^ (row * 73856093) ^ (col * 19349663), 0x9e3779b1);
        dark = ((mixed >>> 13) & 0xff) < 122;
      }
      if (dark) parts.push(`M${col} ${row}h1v1h-1z`);
    }
  }
  return parts.join("");
}

export default function QrPreview({
  value,
  dotsizeMm,
  ec,
  pxPerMm,
}: QrPreviewProps) {
  const modules = qrModuleCount(value, ec);
  const path = useMemo(() => darkModulePath(value, modules), [value, modules]);
  const sidePx = mmToPx(modules * (dotsizeMm > 0 ? dotsizeMm : 0.5), pxPerMm);

  return (
    <svg
      width={sidePx}
      height={sidePx}
      viewBox={`0 0 ${modules} ${modules}`}
      className="block"
      shapeRendering="crispEdges"
      role="img"
      aria-label={`QR placeholder, ${modules}x${modules} modules`}
    >
      <rect width={modules} height={modules} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
}
