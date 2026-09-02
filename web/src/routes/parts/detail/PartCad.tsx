/**
 * The part's CAD tab — which KiCad symbol, footprint, 3D models and
 * SPICE model it uses, plus the schematic fields that ride along.
 *
 * Two ways to name a symbol or footprint, and the UI makes the choice
 * explicit rather than inferring it: "Hosted" picks something this
 * workspace has uploaded, "External" is a `LibNick:Entry` string into
 * the libraries the user already has installed, and "None" falls back to
 * the part's category default. The server rejects setting both halves of
 * a slot, so the radio group is the client-side half of that contract.
 *
 * Saving is a single PUT that REPLACES the configuration — the form
 * posts every field every time, which is what makes "clear the symbol"
 * expressible at all. See `app/domain/eda/schemas.py::PartEdaIn`.
 */
import { useEffect, useRef, useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import {
  EdaDatafilesListSchema,
  EdaFootprintModelsListSchema,
  EdaFootprintsListSchema,
  EdaSymbolsListSchema,
  PartEdaImportSchema,
  PartEdaSchema,
} from "@/lib/schemas";
import { InlineQueryError, type QueryLike } from "@/components/QueryStateBoundary";
import type {
  EdaDatafile,
  EdaFootprint,
  EdaFootprintModel,
  EdaSymbol,
  Part,
  PartEda,
  PartEdaImport,
  PartEdaWrite,
} from "@/types";

/** Which half of a symbol/footprint slot the user is filling in. */
type RefMode = "none" | "hosted" | "external";

function modeFor(id: string | null, external: string | null): RefMode {
  if (id) return "hosted";
  if (external) return "external";
  return "none";
}

/** Comma-separated input ⇄ the array the API carries. */
function splitFilters(text: string): string[] | null {
  const parts = text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length ? parts : null;
}

export default function PartCad() {
  const { part } = useOutletContext<{ part: Part }>();
  const { partId } = useParams<{ partId: string }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();

  const configQuery = useQuery({
    queryKey: useWsKey("part", partId, "eda"),
    queryFn: ({ signal }) =>
      api.parsed.get(`/parts/${partId}/eda`, PartEdaSchema.nullable(), { signal }),
  });
  const symbolsQuery = useQuery({
    queryKey: useWsKey("eda", "symbols"),
    // limit=1000 (the API max): the default 200 would silently truncate
    // the dropdowns once the phase-3 zip importer grows the library.
    queryFn: ({ signal }) =>
      api.parsed.get("/eda/symbols?limit=1000", EdaSymbolsListSchema, { signal }),
  });
  const footprintsQuery = useQuery({
    queryKey: useWsKey("eda", "footprints"),
    queryFn: ({ signal }) =>
      api.parsed.get("/eda/footprints?limit=1000", EdaFootprintsListSchema, { signal }),
  });
  const datafilesQuery = useQuery({
    queryKey: useWsKey("eda", "datafiles"),
    queryFn: ({ signal }) =>
      api.parsed.get("/eda/datafiles?limit=1000", EdaDatafilesListSchema, { signal }),
  });

  const config = configQuery.data ?? null;

  const [symbolMode, setSymbolMode] = useState<RefMode>("none");
  const [symbolId, setSymbolId] = useState("");
  const [symbolExternal, setSymbolExternal] = useState("");
  const [footprintMode, setFootprintMode] = useState<RefMode>("none");
  const [footprintId, setFootprintId] = useState("");
  const [footprintExternal, setFootprintExternal] = useState("");
  const [spiceId, setSpiceId] = useState("");
  const [value, setValue] = useState("");
  const [keywords, setKeywords] = useState("");
  const [filters, setFilters] = useState("");
  const [excludeBom, setExcludeBom] = useState(false);
  const [excludeBoard, setExcludeBoard] = useState(false);
  const [excludeSim, setExcludeSim] = useState(true);
  const [simDevice, setSimDevice] = useState("");
  const [simPins, setSimPins] = useState("");
  const [simParams, setSimParams] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // Seed the form from the server once the config lands, and again each
  // time `seedToken` moves. Keyed on a token rather than a boolean ref:
  // when an import returns a structurally identical config, TanStack
  // hands back the SAME object, the effect never re-runs, and a bare
  // `seeded.current = false` would stay false until some later refetch
  // changed the reference — stomping edits in progress at random.
  const [seedToken, setSeedToken] = useState(0);
  const seededToken = useRef(-1);
  useEffect(() => {
    if (!configQuery.isSuccess || seededToken.current === seedToken) return;
    seededToken.current = seedToken;
    if (!config) return;
    setSymbolMode(modeFor(config.symbol_id, config.symbol_ref_external));
    setSymbolId(config.symbol_id ?? "");
    setSymbolExternal(config.symbol_ref_external ?? "");
    setFootprintMode(modeFor(config.footprint_id, config.footprint_ref_external));
    setFootprintId(config.footprint_id ?? "");
    setFootprintExternal(config.footprint_ref_external ?? "");
    setSpiceId(config.spice_datafile_id ?? "");
    setValue(config.value ?? "");
    setKeywords(config.keywords ?? "");
    setFilters((config.footprint_filters ?? []).join(", "));
    setExcludeBom(config.exclude_from_bom);
    setExcludeBoard(config.exclude_from_board);
    setExcludeSim(config.exclude_from_sim);
    setSimDevice(config.sim_device ?? "");
    setSimPins(config.sim_pins ?? "");
    setSimParams(config.sim_params ?? "");
  }, [config, configQuery.isSuccess, seedToken]);

  const saveMutation = useApiMutation<PartEda, PartEdaWrite>({
    mutationKey: ["part", partId, "eda"],
    mutationFn: (payload) => api.put(`/parts/${partId}/eda`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "eda") });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Failed to save"),
  });

  function save() {
    setErr(null);
    // Only ever send one half of each slot — the server 422s on both,
    // and the radio is what the user actually chose.
    saveMutation.mutate({
      symbol_id: symbolMode === "hosted" ? symbolId || null : null,
      symbol_ref_external: symbolMode === "external" ? symbolExternal.trim() || null : null,
      footprint_id: footprintMode === "hosted" ? footprintId || null : null,
      footprint_ref_external:
        footprintMode === "external" ? footprintExternal.trim() || null : null,
      spice_datafile_id: spiceId || null,
      value: value.trim() || null,
      keywords: keywords.trim() || null,
      footprint_filters: splitFilters(filters),
      exclude_from_bom: excludeBom,
      exclude_from_board: excludeBoard,
      exclude_from_sim: excludeSim,
      sim_device: simDevice.trim() || null,
      sim_pins: simPins.trim() || null,
      sim_params: simParams.trim() || null,
    });
  }

  /**
   * An import rewrites the configuration server-side, so the form has to
   * forget what it seeded and take the new answer.
   *
   * The token is bumped only AFTER the refetch resolves —
   * `invalidateQueries` settles once the query has refetched. Bumping it
   * first would re-seed the form from the config the import just
   * replaced.
   */
  async function reseed() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "eda") });
    await qc.invalidateQueries({
      queryKey: wsKeyOf(workspaceId, "part", partId, "eda"),
    });
    setSeedToken((token) => token + 1);
  }

  const busy = saveMutation.isPending;
  const spiceFiles = (datafilesQuery.data ?? []).filter((d) => d.kind === "spice");

  return (
    <div className="max-w-3xl space-y-4">
      <InlineQueryError query={configQuery} label="EDA configuration" />
      {err && <div className="text-danger text-sm">{err}</div>}

      <VendorZipCard partId={partId ?? ""} onImported={reseed} />
      <LcscCard partId={partId ?? ""} onImported={reseed} />

      <RefSlot
        idPrefix="cad-symbol"
        title="Symbol"
        mode={symbolMode}
        onModeChange={setSymbolMode}
        entries={symbolsQuery.data ?? []}
        entriesQuery={symbolsQuery}
        entriesLabel="symbols"
        selectedId={symbolId}
        onSelectedIdChange={setSymbolId}
        external={symbolExternal}
        onExternalChange={setSymbolExternal}
        externalPlaceholder="Device:R"
        uploadPath="/eda/symbols"
        uploadAccept=".kicad_sym"
        onUploaded={(id) => {
          setSymbolMode("hosted");
          setSymbolId(id);
        }}
        invalidateKey={wsKeyOf(workspaceId, "eda", "symbols")}
      />

      <RefSlot
        idPrefix="cad-footprint"
        title="Footprint"
        mode={footprintMode}
        onModeChange={setFootprintMode}
        entries={footprintsQuery.data ?? []}
        entriesQuery={footprintsQuery}
        entriesLabel="footprints"
        selectedId={footprintId}
        onSelectedIdChange={setFootprintId}
        external={footprintExternal}
        onExternalChange={setFootprintExternal}
        externalPlaceholder="Resistor_SMD:R_0402_1005Metric"
        uploadPath="/eda/footprints"
        uploadAccept=".kicad_mod"
        onUploaded={(id) => {
          setFootprintMode("hosted");
          setFootprintId(id);
        }}
        invalidateKey={wsKeyOf(workspaceId, "eda", "footprints")}
      />

      <FootprintModels
        footprintId={footprintMode === "hosted" ? footprintId : ""}
        datafiles={datafilesQuery.data ?? []}
      />

      <section className="card p-4 space-y-3">
        <h3 className="text-md font-semibold">Simulation (SPICE)</h3>
        <div>
          <label className="label" htmlFor="cad-spice">Model file</label>
          <InlineQueryError query={datafilesQuery} label="data files" className="mb-2" />
          <select
            id="cad-spice"
            className="input"
            value={spiceId}
            onChange={(e) => setSpiceId(e.target.value)}
          >
            <option value="">— none —</option>
            {spiceFiles.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
          <UploadButton
            path="/eda/datafiles"
            accept=".lib,.sub,.cir,.mod,.spice"
            label="Upload SPICE model"
            invalidateKey={wsKeyOf(workspaceId, "eda", "datafiles")}
            onUploaded={(id) => setSpiceId(id)}
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={excludeSim}
            onChange={(e) => setExcludeSim(e.target.checked)}
          />
          Exclude from simulation
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="cad-sim-device">Sim.Device</label>
            <input
              id="cad-sim-device"
              className="input"
              value={simDevice}
              onChange={(e) => setSimDevice(e.target.value)}
              placeholder="R"
            />
          </div>
          <div>
            <label className="label" htmlFor="cad-sim-pins">Sim.Pins</label>
            <input
              id="cad-sim-pins"
              className="input"
              value={simPins}
              onChange={(e) => setSimPins(e.target.value)}
              placeholder="1=+ 2=-"
            />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="cad-sim-params">Sim.Params</label>
          <input
            id="cad-sim-params"
            className="input"
            value={simParams}
            onChange={(e) => setSimParams(e.target.value)}
            placeholder="r=10k"
          />
        </div>
      </section>

      <section className="card p-4 space-y-3">
        <h3 className="text-md font-semibold">Schematic fields</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="cad-value">Value</label>
            <input
              id="cad-value"
              className="input"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={part.name ?? "10k"}
            />
          </div>
          <div>
            <label className="label" htmlFor="cad-keywords">Keywords</label>
            <input
              id="cad-keywords"
              className="input"
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="resistor smd"
            />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="cad-filters">Footprint filters</label>
          <input
            id="cad-filters"
            className="input"
            value={filters}
            onChange={(e) => setFilters(e.target.value)}
            placeholder="R_*, *_0402_*"
          />
          <p className="text-muted text-xs mt-1">
            Comma-separated globs that narrow KiCad's footprint chooser.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={excludeBom}
            onChange={(e) => setExcludeBom(e.target.checked)}
          />
          Exclude from BOM
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={excludeBoard}
            onChange={(e) => setExcludeBoard(e.target.checked)}
          />
          Exclude from board
        </label>
      </section>

      <button className="btn-primary" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------
// Vendor imports
//
// Both cards hit an endpoint that creates library rows AND wires them
// into this part in one request, so both re-seed the form afterwards
// rather than leaving the slots showing what the user last saved.
// ---------------------------------------------------------------------

/** What both import cards need from their caller. */
type ImportCardProps = {
  partId: string;
  onImported: () => void;
};

/** Renders what an import did — created vs reused, and what it skipped. */
function ImportSummary({ result }: { result: PartEdaImport }) {
  const rows = [result.symbol, result.footprint, ...result.datafiles].filter(
    (row): row is NonNullable<typeof row> => !!row,
  );
  const created = rows.filter((r) => r.created).length;

  return (
    <div className="text-sm space-y-1" role="status">
      <p>
        Imported from <span className="pill">{result.vendor}</span>
      </p>
      <p>{`${created} new, ${rows.length - created} already in the library.`}</p>
      <ul className="text-muted text-xs">
        {rows.map((row) => (
          <li key={row.id}>{row.created ? row.name : `${row.name} (reused)`}</li>
        ))}
      </ul>
      {!result.part_eda_updated && (
        <p className="text-muted text-xs">
          Nothing was wired to this part — its slots were already filled. Tick
          &ldquo;Replace what&rsquo;s already set&rdquo; to overwrite them.
        </p>
      )}
      {result.skipped.length > 0 && (
        <ul className="text-muted text-xs">
          {result.skipped.map((skip) => (
            <li key={`${skip.filename}:${skip.reason}`}>
              {`Skipped ${skip.filename}: ${skip.reason}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VendorZipCard({ partId, onImported }: ImportCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [result, setResult] = useState<PartEdaImport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const importMutation = useApiMutation<PartEdaImport, File>({
    mutationKey: ["part", partId, "eda", "import"],
    mutationFn: async (file) => {
      const form = new FormData();
      form.append("file", file);
      form.append("overwrite", String(overwrite));
      // There is no `api.parsed.upload`, so validate here: every other
      // response in this file is schema-checked and a silent shape drift
      // on the one that rewrites the config would be the worst place for
      // it to hide.
      const raw = await api.upload<unknown>(`/parts/${partId}/eda/import`, form);
      return PartEdaImportSchema.parse(raw);
    },
    onSuccess: (data) => {
      setResult(data);
      onImported();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Import failed"),
  });

  return (
    <section className="card p-4 space-y-3">
      <h3 className="text-md font-semibold">Import from vendor zip</h3>
      <p className="text-muted text-sm">
        A SnapEDA, SamacSys or UltraLibrarian download — the symbol, footprint, 3D
        models and SPICE model it carries are added to the library and wired to
        this part.
      </p>
      {err && <div className="text-danger text-sm">{err}</div>}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={overwrite}
          onChange={(e) => setOverwrite(e.target.checked)}
        />
        Replace what&rsquo;s already set
      </label>
      <input
        ref={inputRef}
        type="file"
        accept=".zip"
        className="hidden"
        aria-label="Import from vendor zip"
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Reset first so re-picking the same file fires onChange again.
          e.target.value = "";
          if (!file) return;
          setErr(null);
          setResult(null);
          importMutation.mutate(file);
        }}
      />
      <button
        className="btn"
        disabled={importMutation.isPending}
        onClick={() => inputRef.current?.click()}
      >
        {importMutation.isPending ? "Importing…" : "Choose zip"}
      </button>
      {result && <ImportSummary result={result} />}
    </section>
  );
}

function LcscCard({ partId, onImported }: ImportCardProps) {
  const [lcscId, setLcscId] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [result, setResult] = useState<PartEdaImport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const fetchMutation = useApiMutation<PartEdaImport, string>({
    mutationKey: ["part", partId, "eda", "fetch-lcsc"],
    mutationFn: (id) =>
      api.parsed.post(`/parts/${partId}/eda/fetch-lcsc`, PartEdaImportSchema, {
        lcsc_id: id,
        overwrite,
      }),
    onSuccess: (data) => {
      setResult(data);
      onImported();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Fetch failed"),
  });

  const trimmed = lcscId.trim().toUpperCase();

  return (
    <section className="card p-4 space-y-3">
      <h3 className="text-md font-semibold">Fetch from LCSC</h3>
      <p className="text-muted text-sm">
        Converts the EasyEDA symbol, footprint and 3D model for an LCSC part
        number into KiCad format.
      </p>
      {err && <div className="text-danger text-sm">{err}</div>}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={overwrite}
          onChange={(e) => setOverwrite(e.target.checked)}
        />
        Replace what&rsquo;s already set
      </label>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="label" htmlFor="cad-lcsc">LCSC part number</label>
          <input
            id="cad-lcsc"
            className="input"
            value={lcscId}
            onChange={(e) => setLcscId(e.target.value)}
            placeholder="C25804"
          />
        </div>
        <button
          className="btn"
          disabled={!trimmed || fetchMutation.isPending}
          onClick={() => {
            setErr(null);
            setResult(null);
            fetchMutation.mutate(trimmed);
          }}
        >
          {fetchMutation.isPending ? "Fetching…" : "Fetch"}
        </button>
      </div>
      {result && <ImportSummary result={result} />}
    </section>
  );
}

// ---------------------------------------------------------------------
// Symbol / footprint slot
// ---------------------------------------------------------------------

type RefSlotProps = {
  idPrefix: string;
  title: string;
  mode: RefMode;
  onModeChange: (mode: RefMode) => void;
  entries: (EdaSymbol | EdaFootprint)[];
  entriesQuery: QueryLike;
  entriesLabel: string;
  selectedId: string;
  onSelectedIdChange: (id: string) => void;
  external: string;
  onExternalChange: (value: string) => void;
  externalPlaceholder: string;
  uploadPath: string;
  uploadAccept: string;
  onUploaded: (id: string) => void;
  invalidateKey: unknown[];
};

function RefSlot({
  idPrefix,
  title,
  mode,
  onModeChange,
  entries,
  entriesQuery,
  entriesLabel,
  selectedId,
  onSelectedIdChange,
  external,
  onExternalChange,
  externalPlaceholder,
  uploadPath,
  uploadAccept,
  onUploaded,
  invalidateKey,
}: RefSlotProps) {
  const options: { mode: RefMode; label: string }[] = [
    { mode: "hosted", label: "Hosted here" },
    { mode: "external", label: "External reference" },
    { mode: "none", label: "None (category default)" },
  ];

  return (
    <section className="card p-4 space-y-3">
      <h3 className="text-md font-semibold">{title}</h3>
      <div role="radiogroup" aria-label={`${title} source`} className="flex flex-wrap gap-4">
        {options.map((option) => (
          <label key={option.mode} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={`${idPrefix}-mode`}
              checked={mode === option.mode}
              onChange={() => onModeChange(option.mode)}
            />
            {option.label}
          </label>
        ))}
      </div>

      {mode === "hosted" && (
        <div>
          <label className="label" htmlFor={`${idPrefix}-select`}>
            {title} from this workspace
          </label>
          <InlineQueryError query={entriesQuery} label={entriesLabel} className="mb-2" />
          <select
            id={`${idPrefix}-select`}
            className="input"
            value={selectedId}
            onChange={(e) => onSelectedIdChange(e.target.value)}
          >
            <option value="">— select —</option>
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.name}</option>
            ))}
          </select>
          <UploadButton
            path={uploadPath}
            accept={uploadAccept}
            label={`Upload ${title.toLowerCase()}`}
            invalidateKey={invalidateKey}
            onUploaded={onUploaded}
          />
        </div>
      )}

      {mode === "external" && (
        <div>
          <label className="label" htmlFor={`${idPrefix}-external`}>
            KiCad library reference
          </label>
          <input
            id={`${idPrefix}-external`}
            className="input"
            value={external}
            onChange={(e) => onExternalChange(e.target.value)}
            placeholder={externalPlaceholder}
          />
          <p className="text-muted text-xs mt-1">
            Names an entry in a library you already have installed — nothing is stored here.
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------
// 3D models attached to the selected hosted footprint
// ---------------------------------------------------------------------

function FootprintModels({
  footprintId,
  datafiles,
}: {
  footprintId: string;
  datafiles: EdaDatafile[];
}) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [toAdd, setToAdd] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const modelsQuery = useQuery({
    queryKey: useWsKey("eda", "footprint", footprintId, "models"),
    queryFn: ({ signal }) =>
      api.parsed.get(
        `/eda/footprints/${footprintId}/models`,
        EdaFootprintModelsListSchema,
        { signal },
      ),
    enabled: !!footprintId,
  });

  function invalidate() {
    qc.invalidateQueries({
      queryKey: wsKeyOf(workspaceId, "eda", "footprint", footprintId, "models"),
    });
  }

  const linkMutation = useApiMutation<EdaFootprintModel[], string>({
    mutationKey: ["eda", "footprint", footprintId, "models", "link"],
    mutationFn: (datafileId) =>
      api.post(`/eda/footprints/${footprintId}/models`, { datafile_id: datafileId }),
    onSuccess: () => {
      setToAdd("");
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Failed to attach model"),
  });

  const unlinkMutation = useApiMutation<unknown, string>({
    mutationKey: ["eda", "footprint", footprintId, "models", "unlink"],
    mutationFn: (datafileId) =>
      api.delete(`/eda/footprints/${footprintId}/models/${datafileId}`),
    onSuccess: invalidate,
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Failed to detach model"),
  });

  // Only STEP and WRL attach to a footprint; the server rejects a SPICE
  // model here, so it isn't offered.
  const models = datafiles.filter((d) => d.kind === "step" || d.kind === "wrl");
  const byId = new Map(models.map((d) => [d.id, d]));
  const linked = modelsQuery.data ?? [];
  const linkedIds = new Set(linked.map((row) => row.datafile_id));

  return (
    <section className="card p-4 space-y-3">
      <h3 className="text-md font-semibold">3D models</h3>
      {!footprintId ? (
        <p className="text-muted text-sm">
          Select a hosted footprint to attach 3D models to it.
        </p>
      ) : (
        <>
          {err && <div className="text-danger text-sm">{err}</div>}
          <InlineQueryError query={modelsQuery} label="3D models" />
          {linked.length === 0 ? (
            <p className="text-muted text-sm">No 3D models attached.</p>
          ) : (
            <ul className="space-y-1">
              {linked.map((row) => (
                <li key={row.datafile_id} className="flex items-center gap-2 text-sm">
                  <span className="pill">{byId.get(row.datafile_id)?.kind ?? "3d"}</span>
                  <span className="flex-1">
                    {byId.get(row.datafile_id)?.name ?? row.datafile_id}
                  </span>
                  <button
                    className="btn"
                    disabled={unlinkMutation.isPending}
                    onClick={() => {
                      setErr(null);
                      unlinkMutation.mutate(row.datafile_id);
                    }}
                  >
                    Detach
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="label" htmlFor="cad-model-add">Attach a model</label>
              <select
                id="cad-model-add"
                className="input"
                value={toAdd}
                onChange={(e) => setToAdd(e.target.value)}
              >
                <option value="">— select —</option>
                {models
                  .filter((d) => !linkedIds.has(d.id))
                  .map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
              </select>
            </div>
            <button
              className="btn"
              disabled={!toAdd || linkMutation.isPending}
              onClick={() => {
                setErr(null);
                linkMutation.mutate(toAdd);
              }}
            >
              Attach
            </button>
          </div>
          <UploadButton
            path="/eda/datafiles"
            accept=".step,.stp,.wrl"
            label="Upload 3D model"
            invalidateKey={wsKeyOf(workspaceId, "eda", "datafiles")}
            onUploaded={(id) => linkMutation.mutate(id)}
          />
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------
// Upload button
// ---------------------------------------------------------------------

/**
 * A file input styled as a button. Uploads through `api.upload` so the
 * session cookie rides along, then hands the new row's id back to the
 * caller — which is what lets an upload immediately become the selection.
 */
function UploadButton({
  path,
  accept,
  label,
  invalidateKey,
  onUploaded,
}: {
  path: string;
  accept: string;
  label: string;
  invalidateKey: unknown[];
  onUploaded: (id: string) => void;
}) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);

  const uploadMutation = useApiMutation<{ id: string }, File>({
    mutationKey: ["eda", "upload", path],
    mutationFn: (file) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<{ id: string }>(path, form);
    },
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: invalidateKey });
      onUploaded(row.id);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Upload failed"),
  });

  return (
    <div className="mt-2">
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        aria-label={label}
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Reset first so re-picking the same file fires onChange again.
          e.target.value = "";
          if (!file) return;
          setErr(null);
          uploadMutation.mutate(file);
        }}
      />
      <button
        className="btn"
        disabled={uploadMutation.isPending}
        onClick={() => inputRef.current?.click()}
      >
        {uploadMutation.isPending ? "Uploading…" : label}
      </button>
      {err && <div className="text-danger text-sm mt-1">{err}</div>}
    </div>
  );
}
