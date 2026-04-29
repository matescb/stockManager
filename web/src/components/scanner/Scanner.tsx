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

const LICENSE_KEY =
  "ARqyj56BCn1qC4rajxmvbwY8FsD5CN4jq0T+kURLRlAAej9Rrmc8K6YypikELDzmRkxWEa5x8IXzKgKw4FJ5WIVbzzyCb4uY02OlGv0cmxs2TZUhUFar0a52fOs+JMK28wXcYKkRdF5TBJNA8Rnheb8RDK55Ib01/CYq8rrqxQSgCjtVmd/wBbVrpnT9lINbgIp8DSYgH9y5yIp515dPGnuOIms4P0bKICJmSu5qboptMDfqV+xzTWHJZA/d52cVc1dzIAPoZFd7/hUKmVv35+7uh+xVy6Rfa+NEs4SqyMM3LyskVW0VxgA6CJl9mFuO1RMGmoiPdok71U5EE/9039wlXuh74G52TsZWolPKmqGUOqNwqWi+eO5TEd1pwgc20TI8XqzfATKQMC4gCdhHMMG1/iU3Z9nNvbaPpzmPjzHv8MeAq3l73LawxtbQ5FaO2pnIUrJPWkJBxyyiTVCCZ1YGEv3UafNr1OHL6goOF/kg1ALs1Z1w/Lb2poSU5kPRw50PyV6sYRqlYQqcF3H8UdSgfNO4k2tpxqpneyJhlV0b57E2+YE2uuAmCDEoD3pl6MhchsUOCd58Ep4zqQN/3fBDpD9fQ19Anqt3pzHXMhG7bvfQXDcmcgncbz3dwJG9ZhcG7X95OnSxcpeDO52laacIY+qAhqet+Nspl4lg6O6uQLMJzecDfM2c1p8X2PGZI5UujpKXBT1kkpYCmUE3gVqkOLj/y2T+KqChMx9ViBHJp7P5urh4SOYpkyVRdpZljQApUtF0+MifO25Htg/9Zt+HZL56c34aShjrujkX6YKHlE8LVoAEGPHy7Vb/6iOu2aafKRch";

const LICENSED_SYMBOLOGIES = ["Code128", "Code39", "QR", "DataMatrix", "PDF417"] as const;
type LicensedSymbology = (typeof LICENSED_SYMBOLOGIES)[number];

export type ScanResult = { data: string; symbology: string };

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

export default function Scanner({ onScan, className, symbologies }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<{ text: string; kind?: "ready" | "err" }>({
    text: "Initializing…",
  });

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
        const ctx = await DataCaptureContext.forLicenseKey(LICENSE_KEY, {
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
        const enabled = symbologies ?? LICENSED_SYMBOLOGIES;
        for (const key of enabled) {
          if ((Symbology as any)[key] !== undefined) {
            settings.enableSymbology((Symbology as any)[key], true);
          }
        }
        const capture = await BarcodeCapture.forContext(ctx, settings);
        capture.addListener({
          didScan: (_m, session) => {
            const b = (session as any).newlyRecognizedBarcode;
            if (b) onScan({ data: b.data ?? "", symbology: b.symbology ?? "?" });
          },
        });

        const view = await DataCaptureView.forContext(ctx);
        if (containerRef.current) view.connectToElement(containerRef.current);
        const overlay = await BarcodeCaptureOverlay.withBarcodeCapture(capture);
        await view.addOverlay(overlay);

        await camera.switchToDesiredState(FrameSourceState.On);
        setStatus({ text: "Ready — point camera at a barcode", kind: "ready" });
      } catch (err: any) {
        console.error(err);
        setStatus({ text: `Error: ${err?.message ?? err}`, kind: "err" });
      }
    })();

    return () => {
      stopped = true;
      cameraRef?.switchToDesiredState(FrameSourceState.Off).catch(() => {});
      contextRef?.dispose?.().catch?.(() => {});
    };
  }, [onScan, symbologies]);

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
