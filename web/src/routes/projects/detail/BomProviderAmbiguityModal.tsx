import { useEffect, useMemo, useState } from "react";

export type BomProviderCandidate = {
  manufacturer: string;
  mpn: string | null;
  description: string | null;
  source_url: string | null;
  image_url: string | null;
};

export type BomProviderPendingChoice = {
  entry_id: string;
  mpn: string;
  candidates: BomProviderCandidate[];
};

type Props = {
  open: boolean;
  choices: BomProviderPendingChoice[];
  busy?: boolean;
  onClose: () => void;
  onConfirm: (choices: Record<string, string>) => void;
};

export default function BomProviderAmbiguityModal({ open, choices, busy = false, onClose, onConfirm }: Props) {
  const initial = useMemo(() => {
    const next: Record<string, string> = {};
    for (const choice of choices) {
      const first = choice.candidates[0]?.manufacturer;
      if (first) next[choice.entry_id] = first;
    }
    return next;
  }, [choices]);
  const [selected, setSelected] = useState<Record<string, string>>(initial);

  useEffect(() => {
    if (open) setSelected(initial);
  }, [initial, open]);

  if (!open) return null;

  const canSubmit = choices.every(choice => selected[choice.entry_id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="bom-provider-ambiguity-title" className="w-full max-w-3xl rounded-md border border-border bg-panel p-4 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 id="bom-provider-ambiguity-title" className="text-lg font-semibold">Choose manufacturers</h2>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Close</button>
        </div>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
          {choices.map(choice => (
            <section key={choice.entry_id} className="rounded-md border border-border p-3">
              <div className="mb-2 font-mono text-sm">{choice.mpn}</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {choice.candidates.map(candidate => {
                  const id = `${choice.entry_id}-${candidate.manufacturer}`;
                  return (
                    <label key={id} htmlFor={id} className="flex cursor-pointer gap-2 rounded-md border border-border p-2 hover:border-accent">
                      <input
                        id={id}
                        aria-label={candidate.manufacturer}
                        type="radio"
                        name={choice.entry_id}
                        value={candidate.manufacturer}
                        checked={selected[choice.entry_id] === candidate.manufacturer}
                        onChange={() => setSelected(current => ({ ...current, [choice.entry_id]: candidate.manufacturer }))}
                      />
                      <span className="min-w-0">
                        <span className="block font-medium">{candidate.manufacturer}</span>
                        <span className="block truncate text-xs text-muted">{candidate.description || candidate.mpn || ""}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !canSubmit}
            onClick={() => onConfirm(selected)}
          >
            {busy ? "Importing..." : "Import selected"}
          </button>
        </div>
      </div>
    </div>
  );
}
