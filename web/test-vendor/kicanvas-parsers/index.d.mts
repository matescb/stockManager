/**
 * Types for the parser-only KiCanvas bundle (see README.md).
 *
 * Hand-written and deliberately partial: it declares the handful of
 * members `kicanvasContract.test.ts` reads, not KiCanvas's full model.
 * Upstream ships no types, and mirroring classes this large would create
 * a second thing to keep in sync with a dependency whose whole point here
 * is that we do not track it closely.
 */

/** A `(pin …)` inside a symbol's unit sub-symbol. */
export interface PinDefinition {
  number: { text: string };
  name: { text: string };
}

/** One `(symbol …)` entry, or one of its unit sub-symbols. */
export interface LibSymbol {
  name: string;
  /** Unit sub-symbols — `NAME_0_1` (body graphics), `NAME_1_1` (pins), … */
  children: LibSymbol[];
  drawings: unknown[];
  pins: PinDefinition[];
  properties: Map<string, { name: string; text: string }>;
}

export interface SchematicSymbol {
  lib_id: string;
  /** Resolved by name out of `lib_symbols`; undefined means "draws blank". */
  readonly lib_symbol?: LibSymbol;
  get_property_text(name: string): string | undefined;
}

export declare class KicadSch {
  constructor(filename: string, expr: string);
  lib_symbols?: { symbols: Map<string, LibSymbol> };
  /** Placements, keyed by uuid. */
  symbols: Map<string, SchematicSymbol>;
}

export interface Pad {
  number: string;
  type: string;
  /** Raw layer tokens, wildcards (`*.Cu`) included. */
  layers: string[];
}

export interface Footprint {
  /** The footprint's own name — `(footprint "NAME" …)`. */
  library_link: string;
  layer: string;
  descr: string;
  pads: Pad[];
  drawings: unknown[];
}

export interface Layer {
  ordinal: number;
  canonical_name: string;
  type: string;
}

export declare class KicadPCB {
  constructor(filename: string, expr: string);
  layers: Layer[];
  footprints: Footprint[];
}
