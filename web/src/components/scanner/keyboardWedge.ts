/**
 * USB / Bluetooth "keyboard wedge" barcode readers type the decoded code
 * as keystrokes followed by Enter. `WedgeBuffer` tells such a burst apart
 * from a human typing: every key of a scan arrives within a few ms of the
 * previous one, so a gap longer than `maxGapMs` starts a new buffer.
 *
 * DataMatrix bag codes carry ASCII control separators (RS 0x1e, GS 0x1d,
 * EOT 0x04). Readers in "function key" mode send them as Ctrl+^, Ctrl+]
 * and Ctrl+D; they are mapped back here so `bagSignature()` sees the same
 * raw bytes the camera decoder would have produced.
 */

const CTRL_KEYS: Record<string, string> = {
  "]": "\x1d", // GS
  "^": "\x1e", // RS
  "6": "\x1e", // Ctrl+Shift+6 on US layouts
  d: "\x04", // EOT
  D: "\x04",
};

export type WedgeKey = { key: string; ctrlKey?: boolean; time: number };

export class WedgeBuffer {
  private buf = "";
  private last = -Infinity;

  constructor(
    private readonly maxGapMs = 50,
    private readonly minLength = 4,
  ) {}

  /** Feed one keydown. Returns the scanned code when Enter ends a burst. */
  push(k: WedgeKey): string | null {
    const gap = k.time - this.last;
    this.last = k.time;
    if (gap > this.maxGapMs) this.buf = "";
    if (k.key === "Enter" || k.key === "Tab") {
      const code = this.buf;
      this.buf = "";
      return code.length >= this.minLength ? code : null;
    }
    if (k.ctrlKey) {
      const ch = CTRL_KEYS[k.key];
      if (ch) this.buf += ch;
      return null;
    }
    if (k.key.length === 1) this.buf += k.key;
    return null;
  }
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/** Attach a wedge listener to `window`; returns the detach function.
 *  Keys typed into a form field are left alone — the field gets them. */
export function listenForWedgeScans(onScan: (code: string) => void): () => void {
  const buffer = new WedgeBuffer();
  const handler = (e: KeyboardEvent) => {
    if (isEditable(e.target)) return;
    const code = buffer.push({ key: e.key, ctrlKey: e.ctrlKey, time: e.timeStamp || performance.now() });
    if (code !== null) {
      e.preventDefault();
      onScan(code);
    } else if (e.ctrlKey && CTRL_KEYS[e.key]) {
      // Ctrl+D would otherwise open the bookmark dialog mid-scan.
      e.preventDefault();
    }
  };
  window.addEventListener("keydown", handler);
  return () => window.removeEventListener("keydown", handler);
}
