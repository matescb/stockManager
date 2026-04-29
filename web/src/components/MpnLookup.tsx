import { useState } from "react";
import { Loader2, Search } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { MpnLookupResult } from "@/types";

type Props = {
  mpn: string;
  onResult: (result: NonNullable<MpnLookupResult["result"]>) => void;
};

const PROVIDER_LABEL: Record<string, string> = {
  none: "no provider",
  mouser: "Mouser",
};

/**
 * MpnLookup — small affordance that POSTs the current MPN to
 * `/api/parts/lookup-mpn` and hands the populated record back to the
 * parent via `onResult`. The actual data source is configured per
 * workspace (Settings → Workspace → Parts data provider). The button
 * is disabled while the input is empty or a request is in flight;
 * failures surface inline as a tiny note (network errors are an
 * expected UX here).
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
      const data = await api.post<MpnLookupResult>("/parts/lookup-mpn", { mpn: trimmed });
      const label = PROVIDER_LABEL[data.provider] ?? data.provider;
      if (data.found && data.result) {
        onResult(data.result);
        setNote(`Populated from ${label}`);
      } else {
        setNote(data.message || `No match (${label})`);
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
        title="Look up this MPN against the workspace's configured provider"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        <span>Lookup</span>
      </button>
      {note && <span className="text-xs text-muted">{note}</span>}
    </div>
  );
}
