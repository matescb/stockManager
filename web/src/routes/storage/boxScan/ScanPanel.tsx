/**
 * Input side of box intake / audit: camera scanner (toggleable), USB /
 * Bluetooth keyboard-wedge reader, pasted bag code, and "add by MPN".
 * Scanning a printed box label switches to the same mode for that box.
 */
import { useCallback, useEffect, useState } from "react";
import type React from "react";
import { useNavigate } from "react-router-dom";
import { Camera, CameraOff, Keyboard, Plus, Volume2, VolumeX } from "lucide-react";
import { toast } from "sonner";
import Scanner, { type ScanResult } from "@/components/scanner/Scanner";
import { useKeyboardWedge } from "@/components/scanner/useKeyboardWedge";
import { isScanSoundMuted, playScanSound, primeScanAudio, setScanSoundMuted } from "@/components/scanner/scanSounds";
import { api, ApiError } from "@/lib/api";
import { classifyScan, type ScanSource, type ScannedBag } from "./scanCode";

// 2D only by default: a bag carries several 1D codes (MPN, quantity, order
// number) beside its DataMatrix, and the camera would read whichever it saw
// first as a new bag. While a follow-up asks for one field, the 1D codes are
// exactly what the operator aims at, so they are turned on for that time.
const SYMBOLOGIES_2D = ["DataMatrix", "QR"] as const;
const SYMBOLOGIES_WITH_1D = ["DataMatrix", "QR", "Code128", "Code39"] as const;
const CAMERA_PREF = "boxScan:camera";

function readCameraPref(): boolean {
  try {
    return localStorage.getItem(CAMERA_PREF) !== "off";
  } catch {
    return true;
  }
}

type Props = {
  boxId: string;
  mode: "intake" | "audit";
  onBag: (bag: ScannedBag) => void;
  /** Present only where adding by MPN makes sense. */
  onManual?: (mpn: string, quantity: number) => void;
  /** A follow-up prompt is waiting for one field: the camera also reads 1D. */
  awaitingAnswer?: boolean;
};

export default function ScanPanel({ boxId, mode, onBag, onManual, awaitingAnswer = false }: Props) {
  const navigate = useNavigate();
  const [camera, setCamera] = useState(readCameraPref);
  const [muted, setMuted] = useState(isScanSoundMuted);

  // Browsers only let audio start after a user gesture.
  useEffect(() => primeScanAudio(), []);

  function toggleMuted() {
    setScanSoundMuted(!muted);
    setMuted(!muted);
  }
  const [code, setCode] = useState("");
  const [mpn, setMpn] = useState("");
  const [qty, setQty] = useState("");

  const handle = useCallback(
    async (raw: string, source: ScanSource) => {
      const scanned = await classifyScan(raw, source);
      if (scanned.kind === "bag") {
        onBag(scanned);
        return;
      }
      if (scanned.kind === "unreadable") {
        playScanSound("error");
        toast.error("Code not recognised as a part bag");
        return;
      }
      try {
        const target = await api.get<{ entity_type: string; entity_id: string }>(
          `/codes/${encodeURIComponent(scanned.code)}`,
        );
        if (target.entity_type !== "storage_location") {
          toast.error("That label is not a box");
        } else if (target.entity_id !== boxId) {
          navigate(`/storage/${target.entity_id}/${mode}`);
        }
      } catch (e) {
        toast.error(e instanceof ApiError ? e.userMessage : "Unknown label");
      }
    },
    [boxId, mode, navigate, onBag],
  );

  const onWedge = useCallback((raw: string) => void handle(raw, "wedge"), [handle]);
  useKeyboardWedge(onWedge);
  const onCamera = useCallback((s: ScanResult) => void handle(s.data, "camera"), [handle]);

  function toggleCamera() {
    const next = !camera;
    setCamera(next);
    try {
      localStorage.setItem(CAMERA_PREF, next ? "on" : "off");
    } catch {
      /* private mode — preference just isn't remembered */
    }
  }

  function submitCode(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    void handle(code, "paste");
    setCode("");
  }

  function submitMpn(e: React.FormEvent) {
    e.preventDefault();
    const n = Number(qty);
    if (!mpn.trim() || !Number.isInteger(n) || n <= 0 || !onManual) return;
    onManual(mpn.trim(), n);
    setMpn("");
    setQty("");
  }

  return (
    <div className="card p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-muted inline-flex items-center gap-1.5">
          <Keyboard size={14} /> USB / Bluetooth reader ready — just scan.
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-sm inline-flex items-center gap-1"
            aria-pressed={muted}
            title={muted ? "Scan sounds are off" : "Scan sounds are on"}
            onClick={toggleMuted}
          >
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
            {muted ? "Sound off" : "Sound on"}
          </button>
          <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={toggleCamera}>
            {camera ? <CameraOff size={14} /> : <Camera size={14} />}
            {camera ? "Camera off" : "Camera on"}
          </button>
        </div>
      </div>
      {camera && (
        // Both backends read symbologies when the camera starts, so the key
        // remounts the scanner when the set changes.
        <Scanner
          key={awaitingAnswer ? "with-1d" : "2d"}
          onScan={onCamera}
          symbologies={awaitingAnswer ? SYMBOLOGIES_WITH_1D : SYMBOLOGIES_2D}
          className="flex flex-col h-[40vh]"
        />
      )}
      <form className="flex gap-2" onSubmit={submitCode}>
        <label className="sr-only" htmlFor="box-scan-code">Bag code</label>
        <input
          id="box-scan-code"
          className="input min-w-0 flex-1"
          placeholder="Paste or type a bag code"
          value={code}
          onChange={e => setCode(e.target.value)}
        />
        <button type="submit" className="btn btn-sm" disabled={!code.trim()}>Scan</button>
      </form>
      {onManual && (
        <form className="flex gap-2" onSubmit={submitMpn}>
          <label className="sr-only" htmlFor="box-scan-mpn">MPN</label>
          <input
            id="box-scan-mpn"
            className="input min-w-0 flex-1"
            placeholder="MPN"
            value={mpn}
            onChange={e => setMpn(e.target.value)}
          />
          <label className="sr-only" htmlFor="box-scan-qty">Quantity</label>
          <input
            id="box-scan-qty"
            className="input w-24"
            type="number"
            min={1}
            step={1}
            placeholder="Qty"
            value={qty}
            onChange={e => setQty(e.target.value)}
          />
          <button
            type="submit"
            className="btn-primary btn-sm inline-flex items-center gap-1"
            disabled={!mpn.trim() || !(Number(qty) > 0)}
          >
            <Plus size={14} /> Add by MPN
          </button>
        </form>
      )}
    </div>
  );
}
