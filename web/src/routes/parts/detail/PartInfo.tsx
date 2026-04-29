import { useOutletContext } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileText, ImageOff } from "lucide-react";
import { api } from "@/lib/api";
import type { Part } from "@/types";

type CustomField = { id: string; key: string; value: string | null };

/**
 * Renders the part's identity + stock + description + (when present)
 * the provider-discovered image and datasheet link. Image and
 * datasheet are stored as custom_fields with reserved keys — same
 * place provider-supplied specs live (see Specs tab).
 */
export default function PartInfo() {
  const { part } = useOutletContext<{ part: Part }>();
  const { data: cf } = useQuery({
    queryKey: ["part", part.id, "custom-fields"],
    queryFn: () =>
      api.get<CustomField[]>(`/custom-fields/by-object/part/${part.id}`),
  });
  const lookupBy = (k: string) => cf?.find(r => r.key === k)?.value || null;
  const imageUrl = lookupBy("image_url");
  const datasheetUrl = lookupBy("datasheet_url");

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
      {(imageUrl || datasheetUrl) && (
        <div className="card p-4 col-span-2">
          <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Media</h3>
          <div className="flex items-start gap-4">
            {imageUrl ? (
              <a href={imageUrl} target="_blank" rel="noreferrer" className="shrink-0">
                <img
                  src={imageUrl}
                  alt={part.name}
                  className="h-28 w-28 object-contain rounded border border-border bg-panel2"
                />
              </a>
            ) : (
              <div className="h-28 w-28 rounded border border-border bg-panel2 flex items-center justify-center text-muted">
                <ImageOff size={20} />
              </div>
            )}
            <div className="flex-1 space-y-2 text-sm">
              {datasheetUrl && (
                <a
                  href={datasheetUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-accent hover:underline"
                >
                  <FileText size={14} /> Datasheet <ExternalLink size={12} />
                </a>
              )}
              {imageUrl && (
                <div className="text-xs text-muted break-all">
                  Image: <a className="underline" href={imageUrl} target="_blank" rel="noreferrer">{imageUrl}</a>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
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
