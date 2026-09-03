import { KicanvasFrame } from "./KicanvasFrame";

/**
 * 2D preview of a hosted footprint.
 *
 * Like the symbol preview this points at a synthetic wrapper rather than
 * the stored `.kicad_mod`, because KiCanvas reads boards, not footprint
 * files. See `backend/app/domain/eda/preview.py`.
 */
export function FootprintPreview({ footprintId }: { footprintId: string }) {
  return (
    <KicanvasFrame
      title="Footprint preview"
      src={`/api/eda/footprints/${footprintId}/preview.kicad_pcb`}
    />
  );
}
