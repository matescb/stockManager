import { useEffect, useRef, useState } from "react";
import {
  DataCaptureContext,
  DataCaptureView,
  Camera,
  FrameSourceState,
} from "@scandit/web-datacapture-core";
import {
  barcodeCaptureLoader,
  BarcodeCapture,
  BarcodeCaptureOverlay,
  BarcodeCaptureSettings,
  Symbology,
} from "@scandit/web-datacapture-barcode";

/**
 * Scandit-backed scanner. Opt-in: workspaces that haven't pasted a license
 * key in Settings see the placeholder rendered by the dispatcher (Scanner.tsx)
 * instead of mounting this component, so we never call into the SDK without
 * one. The license key is workspace-scoped and travels in via props.
 *
 * The license is bound to specific origins; the SDK rejects domains the key
 * wasn't provisioned for. See the conversation around licensing for the
 * full picture.
 */

export type ScanResult = { data: string; symbology: string };

const LICENSED_SYMBOLOGIES = ["Code128", "Code39", "QR", "DataMatrix", "PDF417"] as const;
type LicensedSymbology = (typeof LICENSED_SYMBOLOGIES)[number];

type Props = {
  licenseKey: string;
  onScan: (b: ScanResult) => void;
  className?: string;
  symbologies?: ReadonlyArray<LicensedSymbology>;
};

export default function ScanditScanner({ licenseKey, onScan, className, symbologies }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<{ text: string; kind?: "ready" | "err" }>({
    text: "Initializing…",
  });

  // Refs hold the latest props so the SDK init effect can read them at
  // event time without subscribing to them in its dep array. Without
  // this, every parent re-render with a fresh `onScan` closure or
  // `symbologies` array tore the SDK down (`DataCaptureContext.dispose`)
  // and reloaded the multi-MB wasm — visible camera stutter or full
  // soft-lock under any state churn (FE CRIT-2 in the 2026-04-30 review).
  const onScanRef = useRef(onScan);
  useEffect(() => { onScanRef.current = onScan; }, [onScan]);
  const symbologiesRef = useRef(symbologies);
  useEffect(() => { symbologiesRef.current = symbologies; }, [symbologies]);

  useEffect(() => {
    let stopped = false;
    let cameraRef: Camera | null = null;
    let contextRef: DataCaptureContext | null = null;

    (async () => {
      try {
        if (!window.isSecureContext) {
          throw new Error("Not a secure context — open via http://localhost or https.");
        }

        setStatus({ text: "Loading engine…" });
        const ctx = await DataCaptureContext.forLicenseKey(licenseKey, {
          libraryLocation: new URL("/scandit/", window.location.href).toString(),
          moduleLoaders: [barcodeCaptureLoader({ highEndBlurryRecognition: false })],
        });
        contextRef = ctx;
        if (stopped) return;

        setStatus({ text: "Starting camera…" });
        const camera = Camera.pickBestGuess();
        if (!camera) throw new Error("No camera available.");
        cameraRef = camera;
        await camera.setMirrorImageEnabled(false);
        await ctx.setFrameSource(camera);

        const settings = new BarcodeCaptureSettings();
        // symbologies is read at SDK-init time; the ref deref captures
        // whatever the parent had on first mount. If the parent ever
        // wants to change which symbologies are decoded, that requires
        // a remount — keyed-by-symbologies would be the call-site fix,
        // but no caller does that today.
        const enabled = symbologiesRef.current ?? LICENSED_SYMBOLOGIES;
        for (const key of enabled) {
          if ((Symbology as any)[key] !== undefined) {
            settings.enableSymbology((Symbology as any)[key], true);
          }
        }
        const capture = await BarcodeCapture.forContext(ctx, settings);
        capture.addListener({
          didScan: (_m, session) => {
            const b = (session as any).newlyRecognizedBarcode;
            if (b) onScanRef.current({ data: b.data ?? "", symbology: b.symbology ?? "?" });
          },
        });

        const view = await DataCaptureView.forContext(ctx);
        if (containerRef.current) view.connectToElement(containerRef.current);
        const overlay = await BarcodeCaptureOverlay.withBarcodeCapture(capture);
        await view.addOverlay(overlay);

        await camera.switchToDesiredState(FrameSourceState.On);
        setStatus({ text: "Ready — point camera at a barcode", kind: "ready" });
      } catch (err: unknown) {
        // Surface to Sentry so ops sees recurring init failures (license
        // expiry, libraryLocation misconfig, etc.) without depending on
        // someone watching the browser console.
        try {
          const Sentry = await import("@sentry/react");
          Sentry.captureException(err);
        } catch { /* Sentry import failed — non-fatal */ }
        const message = err instanceof Error ? err.message : String(err);
        setStatus({ text: `Error: ${message}`, kind: "err" });
      }
    })();

    return () => {
      stopped = true;
      cameraRef?.switchToDesiredState(FrameSourceState.Off).catch(() => {});
      contextRef?.dispose?.().catch?.(() => {});
    };
    // Only license-key changes warrant a full SDK teardown / re-init.
    // onScan and symbologies flow through refs above.
  }, [licenseKey]);

  return (
    <div className={className ?? "flex flex-col h-[70vh]"}>
      <div ref={containerRef} className="flex-1 bg-black relative overflow-hidden rounded-md" />
      <div className="mt-2 flex items-center gap-2 text-sm">
        <span
          className={
            "w-2.5 h-2.5 rounded-full " +
            (status.kind === "ready" ? "bg-accent" : status.kind === "err" ? "bg-danger" : "bg-muted")
          }
        />
        <span>{status.text}</span>
      </div>
    </div>
  );
}
