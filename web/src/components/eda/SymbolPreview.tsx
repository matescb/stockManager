import { SvgPreview } from "./SvgPreview";

/**
 * 2D preview of a hosted schematic symbol, rendered by kicad-cli.
 *
 * The URL is the backend's SVG render route (`domain/eda/render.py`),
 * served as `image/svg+xml` and shown through an `<img>`. See `SvgPreview`.
 */
export function SymbolPreview({ symbolId }: { symbolId: string }) {
  return (
    <SvgPreview
      title="Symbol preview"
      src={`/api/eda/symbols/${symbolId}/preview.svg`}
    />
  );
}
