import { useOutletContext } from "react-router-dom";
import type { Part } from "@/types";

export default function PartInfo() {
  const { part } = useOutletContext<{ part: Part }>();
  return (
    <div className="grid grid-cols-2 gap-4">
      <div className="card p-4">
        <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Identity</h3>
        <Field label="Name" value={part.name} />
        <Field label="Manufacturer" value={part.manufacturer} />
        <Field label="MPN" value={part.mpn} />
        <Field label="Internal P/N" value={part.internal_part_number} />
        <Field label="Footprint" value={part.footprint} />
      </div>
      <div className="card p-4">
        <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Stock</h3>
        <Field label="On hand" value={String(part.on_hand ?? 0)} />
        <Field label="Low-stock threshold" value={part.low_stock_report_quantity != null ? String(part.low_stock_report_quantity) : null} />
        <Field label="Attrition" value={`${part.attrition_percentage}% (min ${part.attrition_min_quantity})`} />
      </div>
      <div className="card p-4 col-span-2">
        <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Description</h3>
        <p className="whitespace-pre-wrap text-sm">{part.description || <span className="text-muted">—</span>}</p>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="text-sm py-1">
      <span className="text-muted w-40 inline-block">{label}</span>
      <span>{value || <span className="text-muted">—</span>}</span>
    </div>
  );
}
