import { KicanvasFrame } from "./KicanvasFrame";

/**
 * 2D preview of a hosted schematic symbol.
 *
 * The URL is the backend's synthetic-schematic route, not the stored
 * `.kicad_sym` — KiCanvas cannot read a symbol library. The `.kicad_sch`
 * suffix is load-bearing: the viewer types a document by the basename of
 * its URL. See `backend/app/domain/eda/preview.py`.
 */
export function SymbolPreview({ symbolId }: { symbolId: string }) {
  return (
    <KicanvasFrame
      title="Symbol preview"
      src={`/api/eda/symbols/${symbolId}/preview.kicad_sch`}
    />
  );
}
