import { useEffect, useRef, useState } from "react";
import { readBarcodes, prepareZXingModule } from "zxing-wasm/reader";

/**
 * Open-source ZXing-C++ wasm decoder. Default scanner backend so workspaces
 * with no Scandit license still get a working `/parts/scan*` flow.
 *
 * Architecture:
 *   <video>  ← getUserMedia stream (chosen camera + applyConstraints zoom)
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
 *
 * Camera + zoom UX: phones typically expose multiple cameras (rear wide,
 * rear telephoto, ultrawide, front) plus per-track zoom on rear cameras.
 * The picker lets the user choose which one — the chosen `deviceId` is
 * cached in localStorage so reloads remember it. Zoom is exposed as a
 * slider whenever `track.getCapabilities().zoom` reports a range; otherwise
 * it stays hidden (e.g. desktops, Firefox).
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
const DEVICE_PREF_KEY = "scanner.deviceId";

type ZoomCap = { min: number; max: number; step: number };

type Props = {
  onScan: (b: ScanResult) => void;
  className?: string;
  symbologies?: ReadonlyArray<string>;
};

export default function ZxingScanner({ onScan, className, symbologies }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastHitRef = useRef<{ data: string; t: number } | null>(null);
  const trackRef = useRef<MediaStreamTrack | null>(null);
  const [status, setStatus] = useState<{ text: string; kind?: "ready" | "err" }>({
    text: "Initializing…",
  });
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | undefined>(() => {
    try {
      return localStorage.getItem(DEVICE_PREF_KEY) || undefined;
    } catch {
      return undefined;
    }
  });
  const [zoomCap, setZoomCap] = useState<ZoomCap | null>(null);
  const [zoom, setZoom] = useState<number>(1);

  // ---------------------------------------------------------------------
  // Camera + decoder loop. Restarts whenever the picked camera changes.
  // ---------------------------------------------------------------------
  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | null = null;
    let stopped = false;

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
        // Override locateFile so the wasm is fetched from our own /zxing/
        // path (copied there by scripts/copy-zxing-wasm.mjs as a prebuild
        // step) rather than zxing-wasm's default jsDelivr CDN URL. Idempotent
        // across renders — zxing-wasm caches the module promise internally.
        await prepareZXingModule({
          overrides: {
            locateFile: (path: string) =>
              path.endsWith(".wasm") ? "/zxing/zxing_reader.wasm" : path,
          },
          fireImmediately: true,
        });
        if (stopped) return;

        setStatus({ text: "Starting camera…" });
        const constraints: MediaStreamConstraints = {
          video: deviceId
            ? { deviceId: { exact: deviceId } }
            : { facingMode: { ideal: "environment" } },
          audio: false,
        };
        try {
          stream = await navigator.mediaDevices.getUserMedia(constraints);
        } catch (e: any) {
          // The saved deviceId may no longer exist (different host, camera
          // unplugged, etc.). Drop the preference and fall back to defaults.
          if (deviceId && e?.name === "OverconstrainedError") {
            try { localStorage.removeItem(DEVICE_PREF_KEY); } catch {}
            stream = await navigator.mediaDevices.getUserMedia({
              video: { facingMode: { ideal: "environment" } },
              audio: false,
            });
          } else {
            throw e;
          }
        }
        if (stopped) {
          stream.getTracks().forEach(t => t.stop());
          return;
        }
        const video = videoRef.current;
        if (!video) throw new Error("Video element not mounted.");
        video.srcObject = stream;
        await video.play();

        const track = stream.getVideoTracks()[0] ?? null;
        trackRef.current = track;

        // Surface zoom capability for the new track. Browsers that don't
        // implement getCapabilities (or don't expose zoom on this track)
        // hit the else branch and the slider stays hidden.
        const caps = (track && (track as any).getCapabilities?.()) || {};
        if (typeof caps.zoom?.min === "number" && typeof caps.zoom?.max === "number") {
          const cap = {
            min: caps.zoom.min,
            max: caps.zoom.max,
            step: caps.zoom.step || 0.1,
          };
          setZoomCap(cap);
          const settings = ((track as any)?.getSettings?.() || {}) as any;
          setZoom(typeof settings.zoom === "number" ? settings.zoom : cap.min);
        } else {
          setZoomCap(null);
          setZoom(1);
        }

        // Now that permission has been granted, enumerateDevices() returns
        // populated `label` fields. Refresh the picker.
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          if (!stopped) setDevices(all.filter(d => d.kind === "videoinput"));
        } catch { /* non-fatal */ }

        setStatus({ text: "Aim at the code…", kind: "ready" });

        // Reusable offscreen canvas — sized to the video each frame.
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        if (!ctx) throw new Error("2D canvas context unavailable.");

        const tick = async () => {
          if (stopped) return;
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
          if (!stopped) {
            timer = window.setTimeout(tick, SCAN_PERIOD_MS);
          }
        };
        timer = window.setTimeout(tick, SCAN_PERIOD_MS);
      } catch (e: any) {
        if (!stopped) {
          setStatus({ text: e?.message ?? "Failed to start scanner.", kind: "err" });
        }
      }
    })();

    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      if (stream) stream.getTracks().forEach(t => t.stop());
      trackRef.current = null;
      const video = videoRef.current;
      if (video) {
        video.pause();
        video.srcObject = null;
      }
    };
    // Restarting the camera is the whole point of a deviceId change — we want
    // the dep. `symbologies` and `onScan` are intentionally excluded so the
    // loop doesn't tear down when the parent re-renders with a new closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  // ---------------------------------------------------------------------
  // Apply zoom imperatively on the live track when the slider moves.
  // Separated from the camera effect so dragging doesn't restart capture.
  // ---------------------------------------------------------------------
  useEffect(() => {
    const track = trackRef.current;
    if (!track || !zoomCap) return;
    (track as any)
      .applyConstraints({ advanced: [{ zoom }] })
      .catch(() => {
        // Some Android Chromes silently reject mid-stream constraint changes
        // when the track is in an interim state (just opened, focusing).
        // Ignore — the next slider tick will retry.
      });
  }, [zoom, zoomCap]);

  function selectDevice(id: string) {
    setDeviceId(id);
    try {
      if (id) localStorage.setItem(DEVICE_PREF_KEY, id);
      else localStorage.removeItem(DEVICE_PREF_KEY);
    } catch { /* ignore quota / disabled storage */ }
  }

  return (
    <div className={className ?? "flex flex-col h-[70vh]"}>
      {(devices.length > 1 || zoomCap) && (
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
          {devices.length > 1 && (
            <label className="flex items-center gap-1.5">
              <span className="text-muted">Camera</span>
              <select
                className="input py-1 max-w-[14rem]"
                value={deviceId ?? ""}
                onChange={e => selectDevice(e.target.value)}
              >
                <option value="">Default</option>
                {devices.map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Camera ${i + 1}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          {zoomCap && (
            <label className="flex items-center gap-2 flex-1 min-w-[10rem] max-w-md">
              <span className="text-muted">Zoom</span>
              <input
                type="range"
                className="flex-1 accent-accent"
                min={zoomCap.min}
                max={zoomCap.max}
                step={zoomCap.step}
                value={zoom}
                onChange={e => setZoom(parseFloat(e.target.value))}
              />
              <span className="tabular-nums w-10 text-right">{zoom.toFixed(1)}×</span>
            </label>
          )}
        </div>
      )}
      <div className="flex-1 relative w-full overflow-hidden rounded-md bg-black">
        <video
          ref={videoRef}
          className="block w-full h-full object-cover"
          playsInline
          muted
          autoPlay
        />
      </div>
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
