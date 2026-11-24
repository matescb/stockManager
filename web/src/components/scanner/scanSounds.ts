/**
 * Scan feedback tones. The camera decoder (ZXing) has played a click on
 * every decode since it shipped; Scandit beeps by itself and USB readers
 * beep in hardware. "Need more info" is a separate, unmistakable cue — two
 * rising tones — for a scan that was read but cannot be used on its own
 * (a bag label with no manufacturer part number): the operator should
 * look at the screen and scan something else.
 *
 * Box intake / audit also report the outcome of a scan: a short beep when
 * it resolved, a low buzz when it failed (`playScanSound(kind)`).
 *
 * Everything is best-effort: no WebAudio, a suspended context or a phone
 * on silent just means no sound. The mute toggle (box scan pages) is kept
 * in localStorage and silences every tone, the camera click included; the
 * vibration follows it too.
 *
 * Autoplay policy: a context created outside a user gesture starts
 * suspended. `primeScanAudio()` creates / resumes it on the first pointer
 * or key event on the page; until then tones are attempted and may be
 * silent.
 */

const MUTE_KEY = "scanSounds:muted";

export function isScanSoundMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setScanSoundMuted(muted: boolean): void {
  try {
    if (muted) localStorage.setItem(MUTE_KEY, "1");
    else localStorage.removeItem(MUTE_KEY);
  } catch {
    /* private mode — the choice just isn't remembered */
  }
}

let audioCtx: AudioContext | null = null;

/** Create (or resume) the AudioContext on the next user gesture. Returns
 *  a detach function; safe to call from several components. */
export function primeScanAudio(): () => void {
  const onGesture = () => {
    context();
    detach();
  };
  const detach = () => {
    window.removeEventListener("pointerdown", onGesture, true);
    window.removeEventListener("keydown", onGesture, true);
  };
  window.addEventListener("pointerdown", onGesture, true);
  window.addEventListener("keydown", onGesture, true);
  return detach;
}

function context(): AudioContext | null {
  const Ctx: typeof AudioContext | undefined = window.AudioContext ?? window.webkitAudioContext;
  if (!Ctx) return null;
  // Reuse a single AudioContext — Chrome caps the count per tab and every
  // new context is left running until GC.
  if (!audioCtx) audioCtx = new Ctx();
  // iOS / mobile Safari suspends the context until a user gesture; resume
  // on each tone to be safe — no-op if already running.
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

type Tone = { freq: number; at: number; length: number; type?: OscillatorType };

function play(tones: Tone[], vibrate: number | number[]): void {
  if (isScanSoundMuted()) return;
  try {
    const ctx = context();
    if (ctx) {
      for (const t of tones) {
        const start = ctx.currentTime + t.at;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = t.type ?? "square";
        osc.frequency.value = t.freq;
        gain.gain.setValueAtTime(0.08, start);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + t.length);
        osc.connect(gain).connect(ctx.destination);
        osc.start(start);
        osc.stop(start + t.length + 0.01);
      }
    }
  } catch { /* WebAudio unavailable / blocked — skip */ }
  try {
    if (typeof navigator.vibrate === "function") navigator.vibrate(vibrate);
  } catch { /* vibrate API absent — skip */ }
}

/** The normal "code read" click: one short 880 Hz tone + 50 ms buzz. */
export function playScanBeep(): void {
  play([{ freq: 880, at: 0, length: 0.08 }], 50);
}

/** "That scan failed": one low buzz. */
export function playScanError(): void {
  play([{ freq: 160, at: 0, length: 0.28, type: "sawtooth" }], 200);
}

/** "Read it, but I need another scan": two rising tones + double buzz. */
export function playNeedMoreInfo(): void {
  play(
    [
      { freq: 660, at: 0, length: 0.12, type: "sine" },
      { freq: 990, at: 0.15, length: 0.16, type: "sine" },
    ],
    [60, 60, 60],
  );
}

export type ScanSoundKind = "success" | "error" | "needs_info";

/** One entry point for the scan pages (and their tests to mock). */
export function playScanSound(kind: ScanSoundKind): void {
  if (kind === "success") playScanBeep();
  else if (kind === "error") playScanError();
  else playNeedMoreInfo();
}
