import { X } from "lucide-react";

export type BomProviderFailure = {
  entry_id: string;
  mpn: string;
  reason: string;
};

type Props = {
  failures: BomProviderFailure[];
  onClose: () => void;
};

export default function BomProviderFailuresPanel({ failures, onClose }: Props) {
  if (failures.length === 0) return null;

  return (
    <div className="rounded-md border border-danger/40 bg-danger/10 p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-danger">Provider import failures</h3>
        <button type="button" className="btn p-1" onClick={onClose} aria-label="Clear provider import failures">
          <X size={15} />
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-muted">
              <th className="py-1 pr-3 font-medium">MPN</th>
              <th className="py-1 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {failures.map(failure => (
              <tr key={failure.entry_id} className="border-t border-border/70 align-top">
                <td className="py-1.5 pr-3 font-mono text-xs">{failure.mpn || "—"}</td>
                <td className="py-1.5">{failure.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
