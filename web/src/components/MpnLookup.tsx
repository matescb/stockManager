import { useState } from "react";
import { Loader2, Search } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { TrustedPartsResult } from "@/types";

type Props = {
  mpn: string;
  onResult: (result: NonNullable<TrustedPartsResult["result"]>) => void;
};

/**
 * MpnLookup — small affordance that POSTs the current MPN to
 * `/api/trustedparts/lookup` and hands the populated record back to the
 * parent via `onResult`. The button is disabled while the input is
 * empty or a request is in flight; failures surface inline as a tiny
 * note (network errors are an expected UX here).
 */
export default function MpnLookup({ mpn, onResult }: Props) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function run() {
    const trimmed = mpn.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setNote(null);
    try {
      const data = await api.post<TrustedPartsResult>("/trustedparts/lookup", { mpn: trimmed });
      if (data.found && data.result) {
        onResult(data.result);
        setNote("Populated from TrustedParts");
      } else {
        setNote(data.message || "No match found");
      }
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "Lookup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        className="btn flex items-center gap-1"
        onClick={run}
        disabled={busy || !mpn.trim()}
        title="Look up this MPN on TrustedParts"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        <span>Lookup</span>
      </button>
      {note && <span className="text-xs text-muted">{note}</span>}
    </div>
  );
}
