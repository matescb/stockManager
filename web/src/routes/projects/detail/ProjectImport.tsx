import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";

type Preset = {
  id: string;
  name: string;
  config: {
    separator?: string;
    encoding?: string;
    has_header?: boolean;
    designator_separator?: string;
    mapping?: Mapping[];
  };
};

type PreviewOut = {
  detected_separator: string;
  detected_encoding: string;
  has_header: boolean;
  headers: string[] | null;
  rows: { cells: string[] }[];
};

type Mapping = {
  column_index: number;
  target:
    | "ignore"
    | "quantity"
    | "part"
    | "mpn"
    | "manufacturer"
    | "internal_part_number"
    | "designators"
    | "comments"
    | "footprint"
    | "id_code"
    | "cad_key"
    | "dnp";
};

const TARGETS: Mapping["target"][] = [
  "ignore", "quantity", "part", "mpn", "manufacturer", "internal_part_number",
  "designators", "comments", "footprint", "id_code", "cad_key", "dnp",
];

function autoMap(header: string): Mapping["target"] {
  const h = header.trim().toLowerCase();
  if (["qty", "quantity", "count", "amount"].includes(h)) return "quantity";
  if (["mpn", "manufacturer part number", "manuf p/n", "mfr#", "mfr part #"].includes(h)) return "mpn";
  if (["manufacturer", "mfr", "vendor"].includes(h)) return "manufacturer";
  if (["ipn", "internal part number", "internal p/n"].includes(h)) return "internal_part_number";
  if (["designator", "designators", "reference", "references", "refdes"].includes(h)) return "designators";
  if (["comment", "comments", "value", "description"].includes(h)) return h.startsWith("desc") ? "comments" : "comments";
  if (["footprint", "package"].includes(h)) return "footprint";
  if (["dnp", "do not place"].includes(h)) return "dnp";
  if (["part", "name"].includes(h)) return "part";
  if (h === "id" || h === "id code") return "id_code";
  if (h === "cad" || h === "cad key" || h === "cadkey") return "cad_key";
  return "ignore";
}

async function fileToBase64(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

export default function ProjectImport() {
  const { projectId } = useParams<{ projectId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState<"upload" | "mapping" | "done">("upload");
  const [b64, setB64] = useState("");
  const [separator, setSeparator] = useState<string>(",");
  const [encoding, setEncoding] = useState("utf-8");
  const [hasHeader, setHasHeader] = useState(true);
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [mapping, setMapping] = useState<Mapping[]>([]);
  const [designatorSep, setDesignatorSep] = useState(",");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ inserted: number; matched: number; unmatched: number } | null>(null);
  const { data: presets, refetch: refetchPresets } = useQuery({
    queryKey: ["bom-presets"],
    queryFn: () => api.get<Preset[]>("/bom-presets"),
  });

  function applyPreset(p: Preset) {
    if (p.config.separator) setSeparator(p.config.separator);
    if (p.config.encoding) setEncoding(p.config.encoding);
    if (p.config.has_header !== undefined) setHasHeader(!!p.config.has_header);
    if (p.config.designator_separator) setDesignatorSep(p.config.designator_separator);
    if (p.config.mapping) setMapping(p.config.mapping);
  }

  async function savePreset() {
    const name = prompt("Save current mapping as preset — name?");
    if (!name) return;
    try {
      await api.post("/bom-presets", {
        name,
        config: {
          separator,
          encoding,
          has_header: hasHeader,
          designator_separator: designatorSep,
          mapping,
        },
      });
      refetchPresets();
      toast.success(`Preset "${name}" saved.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to save preset");
    }
  }

  async function deletePreset(id: string) {
    if (!confirm("Delete this preset?")) return;
    await api.delete(`/bom-presets/${id}`);
    refetchPresets();
    toast.success("Preset deleted.");
  }

  async function onFile(f: File) {
    setBusy(true);
    setErr(null);
    try {
      const text_b64 = await fileToBase64(f);
      setB64(text_b64);
      const out = await api.post<PreviewOut>(`/projects/${projectId}/bom/preview`, { text_b64 });
      setPreview(out);
      setSeparator(out.detected_separator);
      setEncoding(out.detected_encoding);
      setHasHeader(out.has_header);
      const headers = out.headers || (out.rows[0]?.cells ?? []);
      setMapping(headers.map((h, i) => ({ column_index: i, target: autoMap(h ?? "") })));
      setStep("mapping");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed to parse");
    } finally {
      setBusy(false);
    }
  }

  async function reparse() {
    if (!b64) return;
    setBusy(true);
    try {
      const out = await api.post<PreviewOut>(`/projects/${projectId}/bom/preview`, {
        text_b64: b64, separator, encoding, has_header: hasHeader,
      });
      setPreview(out);
      const headers = out.headers || (out.rows[0]?.cells ?? []);
      setMapping(headers.map((h, i) => ({ column_index: i, target: autoMap(h ?? "") })));
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.post<{ inserted: number; matched: number; unmatched: number }>(`/projects/${projectId}/bom/import`, {
        text_b64: b64,
        separator,
        encoding,
        has_header: hasHeader,
        mapping,
        designator_separator: designatorSep,
      });
      setResult(res);
      qc.invalidateQueries({ queryKey: ["project", projectId, "entries"] });
      setStep("done");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  if (step === "done") {
    return (
      <div className="card p-4 max-w-xl space-y-3">
        <h3 className="text-md font-semibold">Import complete</h3>
        <div className="text-sm">Inserted: <span className="tabular-nums">{result?.inserted}</span></div>
        <div className="text-sm">Matched: <span className="tabular-nums text-accent">{result?.matched}</span></div>
        <div className="text-sm">Unmatched: <span className="tabular-nums text-danger">{result?.unmatched}</span></div>
        <div className="flex gap-2">
          <button className="btn-primary" onClick={() => nav(`/projects/${projectId}/bom`)}>Open BOM</button>
          <button className="btn" onClick={() => { setStep("upload"); setPreview(null); setResult(null); }}>Import another</button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {err && <div className="card p-3 text-danger text-sm">{err}</div>}
      {step === "upload" && (
        <div className="card p-4 space-y-3 max-w-xl">
          <h3 className="text-md font-semibold">Step 1 — upload CSV/TSV</h3>
          <input
            type="file"
            accept=".csv,.tsv,.txt"
            onChange={(e) => e.target.files && onFile(e.target.files[0])}
            disabled={busy}
            className="text-sm"
          />
          {busy && <div className="text-muted text-sm">Parsing…</div>}
        </div>
      )}

      {step === "mapping" && preview && (
        <div className="card p-4 space-y-3">
          <h3 className="text-md font-semibold">Step 2 — column mapping & preview</h3>
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="label">Separator</label>
              <select className="input" value={separator} onChange={e => setSeparator(e.target.value)}>
                <option value=",">, (comma)</option>
                <option value=";">; (semicolon)</option>
                <option value={"\t"}>tab</option>
                <option value="|">| (pipe)</option>
              </select>
            </div>
            <div>
              <label className="label">Encoding</label>
              <input className="input" value={encoding} onChange={e => setEncoding(e.target.value)} />
            </div>
            <div>
              <label className="label">First row is header</label>
              <select className="input" value={hasHeader ? "yes" : "no"} onChange={e => setHasHeader(e.target.value === "yes")}>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </div>
            <div>
              <label className="label">Designator separator</label>
              <input className="input" value={designatorSep} onChange={e => setDesignatorSep(e.target.value)} />
            </div>
          </div>
          <div className="flex gap-2 items-center">
            <button className="btn" onClick={reparse} disabled={busy}>Re-parse</button>
            <select
              className="input max-w-xs ml-auto"
              value=""
              onChange={(e) => {
                const p = presets?.find(x => x.id === e.target.value);
                if (p) applyPreset(p);
              }}
            >
              <option value="">Load preset…</option>
              {presets?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button type="button" className="btn" onClick={savePreset}>Save preset</button>
            {presets && presets.length > 0 && (
              <details className="relative">
                <summary className="btn list-none">Manage</summary>
                <div className="card absolute right-0 top-full mt-1 z-10 p-2 min-w-[220px] space-y-1">
                  {presets.map(p => (
                    <div key={p.id} className="flex items-center text-sm">
                      <span className="flex-1">{p.name}</span>
                      <button type="button" className="btn-danger text-xs" onClick={() => deletePreset(p.id)}>Delete</button>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>

          <div className="overflow-auto">
            <table className="table">
              <thead>
                <tr>
                  {mapping.map(m => (
                    <th key={m.column_index}>
                      <select
                        className="input"
                        value={m.target}
                        onChange={(e) =>
                          setMapping(map => map.map(x => x.column_index === m.column_index ? { ...x, target: e.target.value as any } : x))
                        }
                      >
                        {TARGETS.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      {preview.headers && <div className="text-xs text-muted mt-1">{preview.headers[m.column_index]}</div>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    {r.cells.map((c, j) => <td key={j}>{c}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-xs text-muted mt-2">Showing up to 12 preview rows.</div>
          </div>

          <div className="flex gap-2">
            <button className="btn-primary" onClick={commit} disabled={busy}>{busy ? "Importing…" : "Import BOM"}</button>
            <button className="btn" onClick={() => setStep("upload")}>Back</button>
          </div>
        </div>
      )}
    </div>
  );
}
