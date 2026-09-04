import { SvgPreview } from "./SvgPreview";

/**
 * 2D preview of a hosted footprint, rendered by kicad-cli.
 *
 * Like the symbol preview this points at the backend's SVG render route
 * (`domain/eda/render.py`), served as `image/svg+xml` and shown through an
 * `<img>`. See `SvgPreview`.
 */
export function FootprintPreview({ footprintId }: { footprintId: string }) {
  return (
    <SvgPreview
      title="Footprint preview"
      src={`/api/eda/footprints/${footprintId}/preview.svg`}
    />
  );
}
