import { afterEach, describe, expect, it, vi } from "vitest";
import { playNeedMoreInfo, playScanBeep } from "./scanSounds";

function fakeAudio() {
  const freqs: number[] = [];
  const node = () => ({ connect: vi.fn().mockReturnThis(), gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() } });
  class Ctx {
    state = "running";
    currentTime = 0;
    destination = {};
    createGain = node;
    createOscillator = () => {
      const o = { ...node(), type: "", start: vi.fn(), stop: vi.fn(), frequency: { value: 0 } };
      queueMicrotask(() => freqs.push(o.frequency.value));
      return o;
    };
  }
  vi.stubGlobal("AudioContext", Ctx);
  return freqs;
}

afterEach(() => vi.unstubAllGlobals());

describe("scan sounds", () => {
  it("plays two rising tones for 'need more info', unlike the single scan click", async () => {
    const freqs = fakeAudio();
    playScanBeep();
    await Promise.resolve();
    expect(freqs).toEqual([880]);
    playNeedMoreInfo();
    await Promise.resolve();
    expect(freqs.slice(1)).toEqual([660, 990]);
  });

  it("is silent, not throwing, without WebAudio", () => {
    vi.stubGlobal("AudioContext", undefined);
    expect(() => playNeedMoreInfo()).not.toThrow();
  });
});
