/**
 * Scan feedback tones. The camera decoder (ZXing) has played a click on
 * every decode since it shipped; Scandit beeps by itself and USB readers
 * beep in hardware. "Need more info" is a separate, unmistakable cue — two
 * rising tones — for a scan that was read but cannot be used on its own
 * (a bag label with no manufacturer part number): the operator should
 * look at the screen and scan something else.
 *
 * Everything is best-effort: no WebAudio, a suspended context or a phone
 * on silent just means no sound. There is no mute setting in the app; the
 * device / browser volume is the control.
 */

let audioCtx: AudioContext | null = null;

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
