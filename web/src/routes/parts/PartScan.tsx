import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Scanner, { ScanResult } from "@/components/scanner/Scanner";
import { api } from "@/lib/api";
import { parseBagCode } from "@/lib/bagCode";
import type { Part } from "@/types";

export default function PartScan() {
  const nav = useNavigate();
  const [last, setLast] = useState<ScanResult | null>(null);
  const [parsedMpn, setParsedMpn] = useState<string>("");
  const [matches, setMatches] = useState<Part[]>([]);
  const [busy, setBusy] = useState(false);

  async function handleScan(s: ScanResult) {
    setLast(s);
    const parsed = parseBagCode(s.data);
    // eslint-disable-next-line no-console
    console.log("[bag scan]", {
      symbology: s.symbology,
      raw: s.data,
      escaped: JSON.stringify(s.data),
      length: s.data.length,
      codepoints: Array.from(s.data, c => c.charCodeAt(0)),
      parsed,
    });
    const lookupKey = (parsed.mpn || s.data).trim();
    setParsedMpn(lookupKey);
    setBusy(true);
    try {
      // first try MPN exact, then fallback to free-text q
      let parts = await api.get<Part[]>(`/parts?mpn=${encodeURIComponent(lookupKey)}`);
      if (parts.length === 0) {
        parts = await api.get<Part[]>(`/parts?q=${encodeURIComponent(lookupKey)}`);
      }
      setMatches(parts);
      if (parts.length === 1) {
        nav(`/parts/${parts[0].id}/info`);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="text-xl font-semibold mb-3">Scan</h1>
      <Scanner onScan={handleScan} className="flex flex-col h-[55vh]" />
      <div className="mt-4 card p-3">
        {!last && <div className="text-muted text-sm">Point camera at a barcode to look up the part.</div>}
        {last && (
          <div>
            <div className="text-sm text-muted">
              Last scan: <span className="font-mono text-text">{last.symbology}</span>{" "}
              <span className="font-mono">{last.data}</span>
            </div>
            {parsedMpn && parsedMpn !== last.data && (
              <div className="text-xs text-muted mt-1">
                Looking up MPN: <span className="font-mono text-text">{parsedMpn}</span>
              </div>
            )}
            {busy && <div className="text-muted text-sm mt-2">Looking up…</div>}
            {!busy && matches.length === 0 && (
              <div className="mt-2 text-sm">
                No part matched.{" "}
                <button className="btn ml-2" onClick={() => nav(`/parts/create`)}>+ Create part</button>
              </div>
            )}
            {!busy && matches.length > 1 && (
              <ul className="mt-2 space-y-1">
                {matches.slice(0, 10).map(p => (
                  <li key={p.id}>
                    <button className="btn w-full text-left" onClick={() => nav(`/parts/${p.id}/info`)}>
                      {p.name} {p.mpn && <span className="text-muted ml-2">— {p.mpn}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
