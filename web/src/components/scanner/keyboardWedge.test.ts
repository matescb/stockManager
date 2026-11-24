import { describe, expect, it } from "vitest";
import { WedgeBuffer } from "./keyboardWedge";

function burst(buf: WedgeBuffer, text: string, start = 1000, step = 5): (string | null)[] {
  return [...text].map((key, i) => buf.push({ key, time: start + i * step }));
}

describe("WedgeBuffer", () => {
  it("returns a fast burst terminated by Enter", () => {
    const buf = new WedgeBuffer();
    burst(buf, "LM358DR");
    expect(buf.push({ key: "Enter", time: 1040 })).toBe("LM358DR");
  });

  it("ignores slow human typing", () => {
    const buf = new WedgeBuffer();
    burst(buf, "LM358DR", 1000, 200);
    expect(buf.push({ key: "Enter", time: 3000 })).toBeNull();
  });

  it("drops bursts shorter than the minimum length", () => {
    const buf = new WedgeBuffer();
    burst(buf, "ab");
    expect(buf.push({ key: "Enter", time: 1012 })).toBeNull();
  });

  it("maps Ctrl separators back to control characters", () => {
    const buf = new WedgeBuffer();
    buf.push({ key: "[", time: 1 });
    buf.push({ key: ")", time: 2 });
    buf.push({ key: ">", time: 3 });
    buf.push({ key: "^", ctrlKey: true, time: 4 });
    burst(buf, "06", 5, 1);
    buf.push({ key: "]", ctrlKey: true, time: 7 });
    burst(buf, "1PX1", 8, 1);
    expect(buf.push({ key: "Enter", time: 13 })).toBe("[)>\x1e06\x1d1PX1");
  });

  it("recognises separators by physical key on non-US layouts", () => {
    // Czech QWERTZ: the reader's Ctrl+] arrives as key ")" code BracketRight.
    const buf = new WedgeBuffer();
    burst(buf, "[)>", 1, 1);
    buf.push({ key: "ˇ", code: "Digit6", ctrlKey: true, time: 4 });
    burst(buf, "06", 5, 1);
    buf.push({ key: ")", code: "BracketRight", ctrlKey: true, time: 7 });
    burst(buf, "1PX1", 8, 1);
    expect(buf.push({ key: "Enter", time: 13 })).toBe("[)>\x1e06\x1d1PX1");
  });

  it("keeps AltGr characters (Ctrl+Alt) as typed", () => {
    // Czech AltGr+X = "#" — Windows reports it with ctrlKey and altKey.
    const buf = new WedgeBuffer();
    buf.push({ key: "K", time: 1 });
    buf.push({ key: "#", code: "KeyX", ctrlKey: true, altKey: true, time: 2 });
    buf.push({ key: "]", code: "KeyG", ctrlKey: true, altKey: true, time: 3 });
    burst(buf, "4486", 4, 1);
    expect(buf.push({ key: "Enter", time: 9 })).toBe("K#]4486");
  });

  it("ignores modifier keys and starts fresh after a pause", () => {
    const buf = new WedgeBuffer();
    burst(buf, "junk", 0);
    buf.push({ key: "Shift", time: 500 });
    burst(buf, "ABCD", 501);
    expect(buf.push({ key: "Enter", time: 520 })).toBe("ABCD");
  });
});
