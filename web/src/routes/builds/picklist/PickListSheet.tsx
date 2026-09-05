/**
 * The printable sheet itself (Track B4) — presentational only.
 *
 * Paper, not a label: this renders an A4 document the browser prints with
 * its own dialog. It deliberately does NOT go through the cab SQUIX /
 * JScript label pipeline (that's for adhesive labels on bins and reels)
 * and pulls in no PDF library.
 *
 * Two sections, in the order an operator uses them:
 *
 * 1. **Pick route** — one block per storage location, in walk order, with
 *    a tick box per pick. This is the sheet's whole reason to exist: the
 *    shelves get walked once even when one part is split across two bins
 *    and one bin serves three parts.
 * 2. **BOM summary** — one row per BOM line, for reconciling at the bench
 *    and for the shortfalls the route section cannot show (a line with no
 *    stock anywhere has no stop).
 */
import type { ReactNode } from "react";
import { formatDateTime, formatQuantity } from "@/lib/format";
import {
  NO_PRINT_CLASS,
  PICKLIST_ROOT_ATTR,
  PICK_LIST_PRINT_CSS,
} from "./printStyles";
import type { PickList, PickListLine, PickListStop } from "./types";

/**
 * Every quantity on this sheet goes through `formatQuantity` with
 * `alwaysShowUnit`, and it is the only surface in the app that opts in.
 *
 * On screen the helper suppresses the default `pcs` — the operator has the
 * page around them to infer from. This is paper, carried away from the
 * screen to the shelves, so each number spells its unit out: "12 pcs", not
 * a bare "12". Fractional quantities render exactly either way; nothing
 * here may truncate a measured quantity to an integer.
 */
const PRINTED_QTY = { alwaysShowUnit: true } as const;

function designators(list: string[]): string | null {
  return list.length ? list.join(", ") : null;
}

export default function PickListSheet({
  data,
  controls,
}: {
  data: PickList;
  controls?: ReactNode;
}) {
  const { build, project, stage, totals } = data;
  const rootProps: Record<string, string> = { [PICKLIST_ROOT_ATTR]: "" };

  return (
    <div {...rootProps}>
      <style>{PICK_LIST_PRINT_CSS}</style>

      {controls && <div className={NO_PRINT_CLASS}>{controls}</div>}

      <div className="picklist-sheet text-sm">
        <header className="mb-4">
          <h1 className="text-lg font-semibold">
            Pick list — {build.name}
            {stage && <> · {stage.name}</>}
          </h1>
          <div className="text-muted text-xs mt-1">
            {project.name} · build quantity {build.quantity} · {build.status}
            {stage && <> · stage {stage.sequence + 1} ({stage.status})</>}
          </div>
          <div className="text-muted text-xs">
            {totals.lines} line{totals.lines === 1 ? "" : "s"} ·{" "}
            {totals.stops} location{totals.stops === 1 ? "" : "s"} to visit ·
            printed {formatDateTime(data.generated_at)}
          </div>
          {stage && (
            <div className="text-muted text-xs mt-1">
              This stage only — quantities are this stage&apos;s share of the
              build&apos;s requirement.
            </div>
          )}
        </header>

        {totals.short_lines > 0 && (
          <p className="picklist-short mb-4 border border-danger/50 px-3 py-2 text-danger">
            {totals.short_lines} line{totals.short_lines === 1 ? " is" : "s are"}{" "}
            short — there is not enough stock on hand to cover the full
            requirement. See the shortfall column below.
          </p>
        )}

        <PickRoute stops={data.stops} />
        <BomSummary lines={data.lines} showPortion={stage !== null} />
      </div>
    </div>
  );
}

function PickRoute({ stops }: { stops: PickListStop[] }) {
  if (stops.length === 0) {
    return (
      <section className="picklist-section mb-6">
        <h2 className="card-title mb-2">Pick route</h2>
        <p className="text-muted">
          Nothing to pick — no stock on hand for any line on this sheet.
        </p>
      </section>
    );
  }

  return (
    <section className="picklist-section mb-6">
      <h2 className="card-title mb-2">Pick route</h2>
      {stops.map((stop, index) => (
        <div
          key={stop.storage_location_id ?? "unassigned"}
          className="picklist-stop mb-4"
        >
          <h3 className="font-semibold">
            {index + 1}. {stop.storage_location_name}
          </h3>
          <table className="table">
            <thead>
              <tr>
                <th aria-label="Picked" className="w-8" />
                <th>Part</th>
                <th>Designators</th>
                <th>Lot</th>
                <th>Take</th>
                <th>At location</th>
              </tr>
            </thead>
            <tbody>
              {stop.picks.map(pick => (
                <tr key={`${pick.project_entry_id}:${pick.lot_id ?? "no-lot"}`}>
                  <td>
                    <span className="picklist-box inline-block h-3 w-3 border border-borderStrong" />
                  </td>
                  <td>
                    <div className="font-medium">{pick.part_name}</div>
                    {pick.mpn && <div className="text-xs text-muted">{pick.mpn}</div>}
                  </td>
                  <td className="text-xs text-muted">
                    {designators(pick.designators) ?? "—"}
                  </td>
                  <td className="text-xs text-muted">{pick.lot_name ?? "—"}</td>
                  <td className="tabular-nums font-semibold">
                    {formatQuantity(pick.quantity, pick.unit, PRINTED_QTY)}
                  </td>
                  <td className="tabular-nums text-muted">
                    {formatQuantity(pick.available, pick.unit, PRINTED_QTY)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

function BomSummary({
  lines,
  showPortion,
}: {
  lines: PickListLine[];
  showPortion: boolean;
}) {
  return (
    <section className="picklist-summary">
      <h2 className="card-title mb-2">BOM summary</h2>
      {lines.length === 0 ? (
        <p className="text-muted">No consumable BOM lines.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Part</th>
              <th>Designators</th>
              {showPortion && <th>Stage share</th>}
              <th>Required</th>
              <th>On hand</th>
              <th>Picked</th>
              <th>Short</th>
              <th>Locations</th>
            </tr>
          </thead>
          <tbody>
            {lines.map(line => (
              <tr key={line.project_entry_id}>
                <td>
                  <div className="font-medium">{line.part_name}</div>
                  {line.mpn && <div className="text-xs text-muted">{line.mpn}</div>}
                </td>
                <td className="text-xs text-muted">
                  {designators(line.designators) ?? "—"}
                </td>
                {showPortion && (
                  <td className="tabular-nums text-muted">
                    {line.portion_pct === null ? "—" : `${line.portion_pct}%`}
                  </td>
                )}
                {/* `required` is already attrition-adjusted and ceil-rounded
                    by the server's `_required` — never recompute it here. */}
                <td className="tabular-nums">{formatQuantity(line.required, line.unit, PRINTED_QTY)}</td>
                <td className="tabular-nums">{formatQuantity(line.on_hand, line.unit, PRINTED_QTY)}</td>
                <td className="tabular-nums">{formatQuantity(line.planned, line.unit, PRINTED_QTY)}</td>
                <td
                  className={`tabular-nums ${line.is_short ? "picklist-short text-danger" : ""}`}
                >
                  {line.is_short ? formatQuantity(line.short_by, line.unit, PRINTED_QTY) : "—"}
                  {/* Substitutes and meta-part members are never picked
                      from, but a short line should say they exist —
                      otherwise the sheet reads as a blocker for a build
                      the build screen calls covered. */}
                  {line.is_short && line.alternates_available > 0 && (
                    <div className="text-xs font-normal text-muted">
                      {formatQuantity(line.alternates_available, line.unit, PRINTED_QTY)} in
                      substitutes — decide at consume
                    </div>
                  )}
                </td>
                <td className="tabular-nums text-muted">{line.location_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
