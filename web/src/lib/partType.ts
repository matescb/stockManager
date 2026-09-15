/**
 * How a part's type is written in the UI.
 *
 * `part_type` is a four-value column (`linked | local | meta |
 * sub_assembly`) and three places used to render it raw: the part
 * header, the list preview pane, and the parts-list Type column. Raw
 * was both ugly (`sub_assembly`) and incomplete — "linked" says a
 * provider owns the part's fields without saying which one, and the
 * preview pane papered over that with a second pill.
 *
 * The provider is appended to whatever the type is rather than
 * replacing it: a meta-part can carry an MPN and be refreshed, and that
 * describes where its metadata came from, not what the part is. The
 * server keeps `linked` / `local` in step with `linked_provider`
 * (`backend/app/domain/parts/part_type.py`), so `local · Mouser` is not
 * a state the app can reach.
 *
 * Sorting, search and CSV export stay on the raw column — see the
 * `part_type` entry in `routes/parts/partsColumns.tsx`.
 */
import { providerLabel } from "./providers";

/** Only the two fields the label reads, so BOM rows and previews fit too. */
export type PartTypeLike = {
  part_type: string;
  linked_provider?: string | null;
};

/** Spellings that differ from the stored value. */
const SPELLED: Record<string, string> = {
  sub_assembly: "sub-assembly",
};

export function partTypeLabel(part: PartTypeLike): string {
  const base = SPELLED[part.part_type] ?? part.part_type;
  const provider = part.linked_provider?.trim();
  return provider ? `${base} · ${providerLabel(provider)}` : base;
}
