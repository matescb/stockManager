import { useEffect, useRef, useState } from "react";
import { readBarcodes, prepareZXingModule } from "zxing-wasm/reader";

/**
 * Open-source ZXing-C++ wasm decoder. Default scanner backend so workspaces
 * with no Scandit license still get a working `/parts/scan*` flow.
 *
 * Architecture:
 *   <video>  ← getUserMedia stream (chosen camera + applyConstraints zoom
 *      │      in hardware mode; raw frame + center-crop in digital mode)
 *      │
 *      ▼ requestAnimationFrame loop, throttled to ~6 Hz
 *   offscreen <canvas>.drawImage(video, srcRect…) → ctx.getImageData
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
 * Camera + zoom UX. Phones typically expose multiple cameras (rear wide,
 * rear telephoto, ultrawide, front) plus per-track hardware zoom. PC
 * webcams usually expose neither hardware zoom nor multiple cameras. So:
 *
 *  - The picker shows up whenever there's >1 video input. Pick is cached
 *    in localStorage so reloads remember it.
 *  - The zoom slider is ALWAYS visible (whenever the camera is up).
 *    When `track.getCapabilities().zoom` reports a range we drive the
 *    hardware (best quality). Otherwise we crop the source rect in
 *    software ("digital zoom") and the preview gets a CSS `scale()` so
 *    the user sees the same crop the decoder sees. Digital is capped at
 *    4× — past that you're decoding upscaled noise.
 */

export type ScanResult = { data: string; symbology: string };

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

/**
 * Audible + haptic feedback when a code lands. Scandit's SDK does this
 * for free; with the open-source decoder we have to wire it up ourselves.
 * A short 880Hz square-ish tone via WebAudio + a 50ms vibration. Both are
 * best-effort: phones on silent or browsers without WebAudio just skip.
 */
let _audioCtx: AudioContext | null = null;
function scanFeedback(): void {
  try {
    const Ctx: typeof AudioContext | undefined =
      (window as any).AudioContext ?? (window as any).webkitAudioContext;
    if (Ctx) {
      // Reuse a single AudioContext — Chrome caps the count per tab and
      // every new context is left running until GC.
      if (!_audioCtx) _audioCtx = new Ctx();
      const ctx = _audioCtx;
      // iOS / mobile Safari suspends the context until a user gesture; the
      // first tap that mounts the scanner usually counts. Resume on each
      // beep to be safe — no-op if already running.
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      // ~80ms decay envelope so it sounds like a single click, not a tone.
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.08);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.09);
    }
  } catch { /* WebAudio unavailable / blocked — skip */ }
  try {
    if (typeof navigator.vibrate === "function") navigator.vibrate(50);
  } catch { /* vibrate API absent — skip */ }
}

// Software-crop range. Above ~4× the decoder is fed visibly-pixelated input
// and the slider stops being useful.
const DIGITAL_ZOOM: ZoomCap = { min: 1, max: 4, step: 0.1 };

type ZoomCap = { min: number; max: number; step: number };
type ZoomMode = "hardware" | "digital";

type Props = {
  onScan: (b: ScanResult) => void;
  className?: string;
  symbologies?: ReadonlyArray<string>;
};

export default function ZxingScanner({ onScan, className, symbologies }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastHitRef = useRef<{ data: string; t: number } | null>(null);
  const trackRef = useRef<MediaStreamTrack | null>(null);
  // Refs the decoder loop reads each tick — state setters wouldn't reach the
  // closure that was captured when the camera effect first ran.
  const zoomRef = useRef<number>(1);
  const zoomModeRef = useRef<ZoomMode>("digital");

  const [status, setStatus] = useState<{
    text: string;
    kind?: "ready" | "err" | "perm";
  }>({ text: "Initializing…" });
  // Bumping this re-runs the camera effect — the user clicked "Try again"
  // after granting / changing the browser's camera permission.
  const [retryToken, setRetryToken] = useState(0);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | undefined>(() => {
    try {
      return localStorage.getItem(DEVICE_PREF_KEY) || undefined;
    } catch {
      return undefined;
    }
  });
  const [zoomCap, setZoomCap] = useState<ZoomCap | null>(null);
  const [zoomMode, setZoomMode] = useState<ZoomMode>("digital");
  const [zoom, setZoom] = useState<number>(1);

  // Mirror the live zoom values into refs the tick callback can read.
  useEffect(() => { zoomRef.current = zoom; }, [zoom]);
  useEffect(() => { zoomModeRef.current = zoomMode; }, [zoomMode]);

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

        // Decide hardware vs digital zoom for THIS track. Switching cameras
        // can flip the mode (rear may have hw zoom, front may not).
        const caps = (track && (track as any).getCapabilities?.()) || {};
        if (typeof caps.zoom?.min === "number" && typeof caps.zoom?.max === "number") {
          const cap = {
            min: caps.zoom.min,
            max: caps.zoom.max,
            step: caps.zoom.step || 0.1,
          };
          setZoomCap(cap);
          setZoomMode("hardware");
          const settings = ((track as any)?.getSettings?.() || {}) as any;
          setZoom(typeof settings.zoom === "number" ? settings.zoom : cap.min);
        } else {
          setZoomCap(DIGITAL_ZOOM);
          setZoomMode("digital");
          setZoom(1);
        }

        // Now that permission has been granted, enumerateDevices() returns
        // populated `label` fields. Refresh the picker.
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          if (!stopped) setDevices(all.filter(d => d.kind === "videoinput"));
        } catch { /* non-fatal */ }

        setStatus({ text: "Aim at the code…", kind: "ready" });

        // Reusable offscreen canvas — sized per tick (digital zoom changes the
        // crop dimensions; hardware mode keeps it equal to the video frame).
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

          // Hardware mode: the camera already delivers the zoomed-in frame.
          // Digital mode: read a centre-cropped rectangle so the decoder sees
          // exactly what the user thinks they're scanning. We feed the
          // decoder the cropped pixels at native resolution rather than
          // upscaling them — fewer pixels, same true detail.
          const z = zoomRef.current;
          let sx = 0, sy = 0, sw = w, sh = h;
          if (zoomModeRef.current === "digital" && z > 1) {
            sw = Math.max(1, Math.round(w / z));
            sh = Math.max(1, Math.round(h / z));
            sx = Math.round((w - sw) / 2);
            sy = Math.round((h - sh) / 2);
          }
          if (canvas.width !== sw) canvas.width = sw;
          if (canvas.height !== sh) canvas.height = sh;
          ctx.drawImage(video, sx, sy, sw, sh, 0, 0, sw, sh);
          const imageData = ctx.getImageData(0, 0, sw, sh);
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
                scanFeedback();
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
        if (stopped) return;
        // Map the standardised DOMException names to user-friendly UI
        // states. NotAllowedError fires when the browser-level prompt is
        // denied OR when the site's permission was previously dismissed
        // and the prompt didn't reappear. Both want the same remediation.
        const name = e?.name as string | undefined;
        if (name === "NotAllowedError" || name === "PermissionDeniedError") {
          setStatus({
            text: "Camera access denied — click the camera icon in your browser's address bar to allow it, then Try again.",
            kind: "perm",
          });
        } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
          setStatus({
            text: "No camera was found on this device.",
            kind: "err",
          });
        } else if (name === "NotReadableError" || name === "TrackStartError") {
          setStatus({
            text: "Camera is in use by another app. Close it and Try again.",
            kind: "perm",  // same retry affordance fits
          });
        } else {
          setStatus({
            text: e?.message ?? "Failed to start scanner.",
            kind: "err",
          });
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
    // the dep. retryToken is bumped by the "Try again" button after a
    // permission-denied error, so the user can re-prompt without a full
    // reload. `symbologies` and `onScan` are intentionally excluded so the
    // loop doesn't tear down when the parent re-renders with a new closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, retryToken]);

  // ---------------------------------------------------------------------
  // Apply zoom imperatively on the live track in hardware mode. Digital
  // mode just lives in the ref and the tick callback picks it up.
  // ---------------------------------------------------------------------
  useEffect(() => {
    if (zoomMode !== "hardware") return;
    const track = trackRef.current;
    if (!track) return;
    (track as any)
      .applyConstraints({ advanced: [{ zoom }] })
      .catch(() => {
        // Some Android Chromes silently reject mid-stream constraint changes
        // when the track is in an interim state (just opened, focusing).
        // Ignore — the next slider tick will retry.
      });
  }, [zoom, zoomMode]);

  function selectDevice(id: string) {
    setDeviceId(id);
    try {
      if (id) localStorage.setItem(DEVICE_PREF_KEY, id);
      else localStorage.removeItem(DEVICE_PREF_KEY);
    } catch { /* ignore quota / disabled storage */ }
  }

  // CSS preview transform for digital zoom. Hardware mode skips it because
  // the underlying frame is already zoomed by the camera.
  const previewTransform =
    zoomMode === "digital" && zoom > 1 ? `scale(${zoom})` : undefined;

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
              <span className="text-muted">
                Zoom{zoomMode === "digital" ? " (digital)" : ""}
              </span>
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
        {/* Always mount the video so videoRef stays valid for retry — the
            permission panel just stacks over it when access is denied. */}
        <video
          ref={videoRef}
          className="block w-full h-full object-cover"
          style={{
            transform: previewTransform,
            transformOrigin: "center center",
            imageRendering: previewTransform ? "auto" : undefined,
          }}
          playsInline
          muted
          autoPlay
        />
        {status.kind === "perm" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center p-6 text-center bg-bg-soft text-text">
            <div className="text-2xl mb-2" aria-hidden>📷</div>
            <p className="max-w-sm text-sm mb-4">{status.text}</p>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setRetryToken(t => t + 1)}
            >
              Try again
            </button>
          </div>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm">
        <span
          className={
            "w-2.5 h-2.5 rounded-full " +
            (status.kind === "ready"
              ? "bg-accent"
              : status.kind === "err" || status.kind === "perm"
              ? "bg-danger"
              : "bg-muted")
          }
        />
        <span>{status.text}</span>
      </div>
    </div>
  );
}
