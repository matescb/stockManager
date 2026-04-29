import { useEffect, useRef, useState } from "react";
import { readBarcodes, prepareZXingModule } from "zxing-wasm/reader";

/**
 * Open-source ZXing-C++ wasm decoder. Default scanner backend so workspaces
 * with no Scandit license still get a working `/parts/scan*` flow.
 *
 * Architecture:
 *   <video>  ← getUserMedia stream
 *      │
 *      ▼ requestAnimationFrame loop, throttled to ~6 Hz
 *   offscreen <canvas>.drawImage(video) → ctx.getImageData
 *      │
 *      ▼
 *   readBarcodes(imageData, { formats: [...] })
 *      │
 *      ▼ debounced 1.2s against the previous payload
 *   onScan({ data, symbology })
 *
 * Why we render the video ourselves (Scandit's SDK does this internally):
 * we need pixel access to the current frame anyway, and a visible preview is
 * what makes hand-aiming feasible.
 */

export type ScanResult = { data: string; symbology: string };

// Public symbology names match the Scandit-flavored API the rest of the app
// consumes (`MpnLookup`, `ScanImport`, `PartScan`). `QR` is internally mapped
// to ZXing's `QRCode`, and QRCode results are mapped back to `QR` so callers
// see consistent values regardless of which decoder ran.
const ZXING_FORMAT_BY_PUBLIC: Record<string, string> = {
  Code128: "Code128",
  Code39: "Code39",
  QR: "QRCode",
  DataMatrix: "DataMatrix",
  PDF417: "PDF417",
};
const PUBLIC_NAME_BY_ZXING: Record<string, string> = {
  Code128: "Code128",
  Code39: "Code39",
  QRCode: "QR",
  DataMatrix: "DataMatrix",
  PDF417: "PDF417",
};

const DEFAULT_SYMBOLOGIES = ["Code128", "Code39", "QR", "DataMatrix", "PDF417"];

const SCAN_PERIOD_MS = 160;        // ~6 Hz; ZXing on a recent phone returns in ~30-80ms
const DUPLICATE_WINDOW_MS = 1200;  // suppress repeated reads of the same code

type Props = {
  onScan: (b: ScanResult) => void;
  className?: string;
  symbologies?: ReadonlyArray<string>;
};

export default function ZxingScanner({ onScan, className, symbologies }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const lastHitRef = useRef<{ data: string; t: number } | null>(null);
  const stoppedRef = useRef(false);
  const [status, setStatus] = useState<{ text: string; kind?: "ready" | "err" }>({
    text: "Initializing…",
  });

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | null = null;
    stoppedRef.current = false;

    const enabled = (symbologies && symbologies.length ? symbologies : DEFAULT_SYMBOLOGIES)
      .map(s => ZXING_FORMAT_BY_PUBLIC[s])
      .filter(Boolean);

    (async () => {
      try {
        if (!window.isSecureContext) {
          throw new Error("Not a secure context — open via http://localhost or https.");
        }
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error("Camera API unavailable in this browser.");
        }

        setStatus({ text: "Loading decoder…" });
        // Warm up the wasm so the first decode doesn't pay the load cost.
        // Override locateFile so the wasm is fetched from our own /zxing/
        // path (copied there by scripts/copy-zxing-wasm.mjs as a prebuild
        // step) rather than the package's default jsDelivr CDN URL.
        await prepareZXingModule({
          overrides: {
            locateFile: (path: string) =>
              path.endsWith(".wasm") ? "/zxing/zxing_reader.wasm" : path,
          },
          fireImmediately: true,
        });
        if (stoppedRef.current) return;

        setStatus({ text: "Starting camera…" });
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
        if (stoppedRef.current) {
          stream.getTracks().forEach(t => t.stop());
          return;
        }
        const video = videoRef.current;
        if (!video) throw new Error("Video element not mounted.");
        video.srcObject = stream;
        await video.play();
        setStatus({ text: "Aim at the code…", kind: "ready" });

        // Reusable offscreen canvas — sized to the video each frame.
        const canvas = document.createElement("canvas");
        canvasRef.current = canvas;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        if (!ctx) throw new Error("2D canvas context unavailable.");

        const tick = async () => {
          if (stoppedRef.current) return;
          if (document.visibilityState !== "visible") {
            timer = window.setTimeout(tick, SCAN_PERIOD_MS);
            return;
          }
          const w = video.videoWidth;
          const h = video.videoHeight;
          if (w === 0 || h === 0) {
            timer = window.setTimeout(tick, SCAN_PERIOD_MS);
            return;
          }
          if (canvas.width !== w) canvas.width = w;
          if (canvas.height !== h) canvas.height = h;
          ctx.drawImage(video, 0, 0, w, h);
          const imageData = ctx.getImageData(0, 0, w, h);
          try {
            const results = await readBarcodes(imageData, {
              formats: enabled as any,
              tryHarder: true,
              tryRotate: true,
              tryInvert: true,
            });
            const hit = results?.find(r => r.text);
            if (hit && hit.text) {
              const now = Date.now();
              const last = lastHitRef.current;
              const isDup = last && last.data === hit.text && now - last.t < DUPLICATE_WINDOW_MS;
              if (!isDup) {
                lastHitRef.current = { data: hit.text, t: now };
                const sym = PUBLIC_NAME_BY_ZXING[hit.format] ?? hit.format ?? "?";
                onScan({ data: hit.text, symbology: sym });
              }
            }
          } catch {
            // Decoder errors are routine (unreadable frame, occlusion). Just
            // try again on the next tick — never tear the loop down for them.
          }
          if (!stoppedRef.current) {
            timer = window.setTimeout(tick, SCAN_PERIOD_MS);
          }
        };
        timer = window.setTimeout(tick, SCAN_PERIOD_MS);
      } catch (e: any) {
        if (!stoppedRef.current) {
          setStatus({ text: e?.message ?? "Failed to start scanner.", kind: "err" });
        }
      }
    })();

    return () => {
      stoppedRef.current = true;
      if (timer) window.clearTimeout(timer);
      if (stream) stream.getTracks().forEach(t => t.stop());
      const video = videoRef.current;
      if (video) {
        video.pause();
        video.srcObject = null;
      }
    };
    // symbologies array identity is stable per-render at the call sites; we
    // don't restart the loop when it shifts. If a caller ever flips it
    // dynamically, that will be the moment to add `symbologies` to deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={className}>
      <div className="relative w-full overflow-hidden rounded bg-black">
        <video
          ref={videoRef}
          className="block w-full"
          playsInline
          muted
          autoPlay
        />
      </div>
      <div
        className={
          "mt-2 text-xs " +
          (status.kind === "err"
            ? "text-red-500"
            : status.kind === "ready"
            ? "text-text-muted"
            : "text-text-muted")
        }
      >
        {status.text}
      </div>
    </div>
  );
}
