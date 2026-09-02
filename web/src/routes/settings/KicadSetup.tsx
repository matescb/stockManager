import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { KicadSetupSchema } from "@/lib/schemas";
import { useWsKey } from "@/lib/queryKeys";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { KicadSetup } from "@/types";

const HTTPLIB_FILENAME = "stockmanager.kicad_httplib";

/**
 * `meta.version` is the one value in a `.kicad_httplib` KiCad reads as a
 * number, and `1` is not the same as `1.0` to a reader expecting a
 * float. `JSON.stringify({ version: 1.0 })` emits `1`, which is why the
 * file below is assembled from a template rather than serialised.
 */
function formatMetaVersion(value: number): string {
  return Number.isInteger(value) ? value.toFixed(1) : String(value);
}

/**
 * Compose the client library file from the server's example and the
 * token the user pasted.
 *
 * The merge happens here, in the browser, because it cannot happen on
 * the server: a token's plaintext exists exactly once, in the response
 * that minted it, so `/eda/kicad-setup` can only ever hand out the file
 * with a placeholder where the secret goes.
 */
export function buildHttpLibFile(setup: KicadSetup, token: string): string {
  const { meta, name, source } = setup.example;
  return [
    "{",
    `  "meta": { "version": ${formatMetaVersion(meta.version)} },`,
    `  "name": ${JSON.stringify(name)},`,
    '  "source": {',
    `    "type": ${JSON.stringify(source.type)},`,
    `    "api_version": ${JSON.stringify(source.api_version)},`,
    `    "root_url": ${JSON.stringify(source.root_url)},`,
    `    "token": ${JSON.stringify(token)},`,
    `    "timeout_parts_seconds": ${source.timeout_parts_seconds},`,
    `    "timeout_categories_seconds": ${source.timeout_categories_seconds}`,
    "  }",
    "}",
    "",
  ].join("\n");
}

/** Substitute the pasted token into the repository URL template. */
export function buildPcmUrl(setup: KicadSetup, token: string): string {
  if (!token) return setup.pcm_repository_url_template;
  // The placeholder is the last path segment before the document name,
  // so replacing it by value is unambiguous.
  return setup.pcm_repository_url_template.replace(
    /\/[^/]+\/repository\.json$/,
    `/${encodeURIComponent(token)}/repository.json`,
  );
}

async function copy(value: string, what: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${what} copied to clipboard.`);
  } catch {
    // Denied in plenty of legitimate contexts (insecure origin,
    // permissions policy). Everything here is on screen and selectable,
    // so copying is a convenience rather than the only route.
    toast.error("Couldn't copy — select the text and copy it manually.");
  }
}

function CopyButton({ value, what }: { value: string; what: string }) {
  return (
    <button type="button" className="btn text-xs" onClick={() => copy(value, what)}>
      Copy
    </button>
  );
}

function Field({ value }: { value: string }) {
  return (
    <code className="block font-mono text-xs break-all rounded bg-panel2 p-2">{value}</code>
  );
}

export default function KicadSetupSettings() {
  // One input feeds both the library file and the PCM URL. They could
  // take different tokens, but the PCM surface accepts read-only ones
  // alone and the HTTP library is all GETs — so a single read-only
  // token is the right credential for both, and asking twice for it
  // would only invite pasting a full-access token into the half that
  // refuses it.
  const [token, setToken] = useState("");

  const setupQuery = useQuery({
    queryKey: useWsKey("eda", "kicad-setup"),
    queryFn: ({ signal }) =>
      api.parsed.get("/eda/kicad-setup", KicadSetupSchema, { signal }),
  });
  const setup = setupQuery.data;

  function download() {
    if (!setup) return;
    const blob = new Blob([buildHttpLibFile(setup, token)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = HTTPLIB_FILENAME;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-semibold mb-4">KiCad setup</h1>
      <p className="text-sm text-muted mb-4">
        Two things connect KiCad to this workspace, and you need both. The
        HTTP library puts your parts in KiCad&apos;s symbol chooser; the
        add-on package installs the symbol, footprint and 3D model files
        those entries name. Set them up in that order.
      </p>

      <InlineQueryError query={setupQuery} label="KiCad setup" className="mb-3" />

      <div className="card p-4 mb-4 space-y-3">
        <h2 className="text-md font-semibold">Step 1 — your token</h2>
        <p className="text-sm text-muted">
          Both halves authenticate with one personal access token. Mint a{" "}
          <strong>read-only</strong> one: it ends up in a config file on your
          workstation, and the add-on repository refuses anything else.
        </p>
        <Link className="btn inline-block" to="/settings/api-tokens">
          Mint a read-only token
        </Link>
        <div>
          <label className="label" htmlFor="kicad-token">
            Paste it here
          </label>
          <input
            id="kicad-token"
            className="input font-mono"
            autoComplete="off"
            spellCheck={false}
            placeholder="smk_…"
            value={token}
            onChange={e => setToken(e.target.value)}
          />
          <div className="text-xs text-muted mt-1">
            Stays in this browser tab — it is written into the file you
            download and the URL you copy below, and is never sent back to
            the server.
          </div>
        </div>
      </div>

      {setupQuery.isLoading && <div className="text-muted text-sm">Loading…</div>}

      {setup && (
        <>
          <div className="card p-4 mb-4 space-y-3">
            <h2 className="text-md font-semibold">Step 2 — the HTTP library</h2>
            <ol className="text-sm list-decimal ml-5 space-y-2">
              <li>
                Download the library file and save it somewhere permanent —
                KiCad reads it from that path every time it starts.
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={!token.trim()}
                    onClick={download}
                  >
                    Download {HTTPLIB_FILENAME}
                  </button>
                  {!token.trim() && (
                    <span className="text-xs text-muted">Paste a token first.</span>
                  )}
                </div>
              </li>
              <li>
                In KiCad, open <strong>Preferences → Manage Symbol Libraries</strong>,
                switch to the <strong>Global Libraries</strong> tab (or Project, if
                you want it for one project only) and click <strong>+</strong>.
              </li>
              <li>
                Set the library&apos;s <strong>Library Path</strong> to the file you
                just saved, and its <strong>Library Format</strong> to{" "}
                <strong>Database/HTTP</strong>. Give it any nickname you like.
              </li>
              <li>
                Reopen the symbol chooser. Your categories appear as
                sub-trees; parts without a category land under{" "}
                <strong>Uncategorized</strong>.
              </li>
            </ol>
            <p className="text-xs text-muted">
              KiCad caches what it fetches: parts for {setup.parts_ttl} seconds and
              categories for {setup.categories_ttl} seconds. An edit made here shows
              up in an already-open chooser once that window passes.
            </p>
            <div>
              <div className="label">Server URL, if you configure it by hand</div>
              <Field value={setup.root_url} />
            </div>
          </div>

          <div className="card p-4 mb-4 space-y-3">
            <h2 className="text-md font-semibold">
              Step 3 — the add-on package (PCM)
            </h2>
            <p className="text-sm text-muted">
              The HTTP library only <em>names</em> symbols and footprints. This
              repository installs the files themselves, so the names resolve.
            </p>
            <ol className="text-sm list-decimal ml-5 space-y-2">
              <li>
                Copy the repository URL below and add it in{" "}
                <strong>Preferences → Manage Plugin and Content Manager
                Repositories</strong>.
                <div className="mt-2 flex items-start gap-2">
                  <div className="flex-1">
                    <Field value={buildPcmUrl(setup, token.trim())} />
                  </div>
                  <CopyButton
                    value={buildPcmUrl(setup, token.trim())}
                    what="Repository URL"
                  />
                </div>
              </li>
              <li>
                Open the Plugin and Content Manager, pick the new repository on the{" "}
                <strong>Libraries</strong> tab, and install the package.
              </li>
              <li>
                When you add or change a symbol here, the package version moves and
                the PCM offers an update. Take it — that is how new parts reach your
                machine.
              </li>
            </ol>
            <div className="rounded border border-warning/50 p-3 text-sm">
              {setup.read_only_note}
            </div>
            <div>
              <div className="label">Package identifier</div>
              <Field value={setup.pcm_package_identifier} />
            </div>
          </div>

          <div className="card p-4 mb-4 space-y-3">
            <h2 className="text-md font-semibold">
              Step 4 — SPICE models (only if you simulate)
            </h2>
            <p className="text-sm text-muted">
              Simulation models are the one reference the package cannot fix up
              for itself, so you point one path variable at the installed
              directory. Skip this if you do not run ngspice.
            </p>
            <ol className="text-sm list-decimal ml-5 space-y-2">
              <li>
                Open <strong>Preferences → Configure Paths</strong>.
              </li>
              <li>
                Add an environment variable with this name and this value:
                <div className="mt-2 space-y-2">
                  <div className="flex items-start gap-2">
                    <div className="flex-1">
                      <Field value={setup.pcm_spice_path_variable} />
                    </div>
                    <CopyButton
                      value={setup.pcm_spice_path_variable}
                      what="Variable name"
                    />
                  </div>
                  <div className="flex items-start gap-2">
                    <div className="flex-1">
                      <Field value={setup.pcm_spice_path_value} />
                    </div>
                    <CopyButton value={setup.pcm_spice_path_value} what="Path" />
                  </div>
                </div>
              </li>
              <li>Restart KiCad so the new variable is picked up.</li>
            </ol>
          </div>

          {setup.mcp_url && (
            <div className="card p-4 space-y-3">
              <h2 className="text-md font-semibold">Not KiCad — AI agents</h2>
              <p className="text-sm text-muted">{setup.mcp_note}</p>
              <div className="flex items-start gap-2">
                <div className="flex-1">
                  <Field value={setup.mcp_url} />
                </div>
                <CopyButton value={setup.mcp_url} what="MCP URL" />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
