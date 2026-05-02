import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { wsKey } from "@/lib/queryKeys";

type WsScanner = {
  scanner: "zxing" | "scandit";
  has_scanner_license_key: boolean;
};

/**
 * Dispatcher: pick the scanner backend the workspace is configured for and
 * mount only that one. Both backends conform to the `Props` shape so call
 * sites (`MpnLookup`, `ScanImport`) don't change.
 *
 * Why lazy: each backend pulls a multi-MB wasm blob. Splitting them keeps
 * the SPA shell small and only fetches the chosen decoder.
 */

export type ScanResult = { data: string; symbology: string };

const LICENSED_SYMBOLOGIES = ["Code128", "Code39", "QR", "DataMatrix", "PDF417"] as const;
export type LicensedSymbology = (typeof LICENSED_SYMBOLOGIES)[number];

type Props = {
  onScan: (b: ScanResult) => void;
  className?: string;
  /**
   * Restrict the recognizer to a subset of symbologies. Useful when a
   * page only wants the 2D code on a distributor bag (the 1D Code128
   * codes printed alongside carry single unstructured fields and lead
   * to junk MPNs). Defaults to all licensed symbologies.
   */
  symbologies?: ReadonlyArray<LicensedSymbology>;
};

const ZxingScanner = lazy(() => import("./ZxingScanner"));
const ScanditScanner = lazy(() => import("./ScanditScanner"));

export default function Scanner({ onScan, className, symbologies }: Props) {
  // Same query key as Settings → Workspace, so the cache is shared across
  // scanner mounts and the settings page.
  const { data: ws, isLoading } = useQuery({
    queryKey: wsKey("ws", "current"),
    queryFn: () => api.get<WsScanner>("/workspaces/current"),
  });

  if (isLoading || !ws) {
    return <div className={className}>Loading scanner…</div>;
  }

  const fallback = <div className={className}>Loading decoder…</div>;

  if (ws.scanner === "scandit") {
    if (!ws.has_scanner_license_key) {
      return (
        <div className={className ?? "flex flex-col h-[70vh]"}>
          {/* `bg-bg-soft` and `text-text-muted` were stale tokens — neither
              exists in `tailwind.config.js`, so the panel rendered with a
              transparent background and inherited text colour. The
              defined tokens are `bg-panel2` and `text-muted` (FE2-009). */}
          <div className="flex-1 flex items-center justify-center bg-panel2 rounded-md p-6 text-center">
            <div className="max-w-sm text-sm text-muted">
              <p className="mb-2 font-medium text-text">Scandit license key missing.</p>
              <p>
                Set one in <strong className="text-text">Settings → Workspace → Scanner</strong>,
                or switch the scanner to the open-source decoder there.
              </p>
            </div>
          </div>
        </div>
      );
    }
    // The license key is fetched at run time. We don't put it on the wire
    // anywhere else; the API exposes only `has_scanner_license_key`. Fetch
    // the raw key separately so it stays scoped to the scanner mount.
    return (
      <Suspense fallback={fallback}>
        <ScanditScannerWithKey
          onScan={onScan}
          className={className}
          symbologies={symbologies}
        />
      </Suspense>
    );
  }

  // Default: open-source ZXing.
  return (
    <Suspense fallback={fallback}>
      <ZxingScanner onScan={onScan} className={className} symbologies={symbologies} />
    </Suspense>
  );
}

/**
 * Wrapper that fetches the Scandit license key from a dedicated endpoint
 * (the workspace serializer never echoes it) and passes it down. Kept as
 * a small inner component so the key fetch is scoped to Scandit users only.
 */
function ScanditScannerWithKey({
  onScan,
  className,
  symbologies,
}: {
  onScan: (b: ScanResult) => void;
  className?: string;
  symbologies?: ReadonlyArray<LicensedSymbology>;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: wsKey("ws", "scanner", "license-key"),
    queryFn: () => api.get<{ license_key: string }>("/workspaces/current/scanner-license-key"),
  });
  if (isLoading) return <div className={className}>Fetching license…</div>;
  if (error || !data?.license_key) {
    return (
      <div className={className}>
        Failed to load Scandit license key. Re-save it in Settings.
      </div>
    );
  }
  return (
    <ScanditScanner
      licenseKey={data.license_key}
      onScan={onScan}
      className={className}
      symbologies={symbologies}
    />
  );
}
