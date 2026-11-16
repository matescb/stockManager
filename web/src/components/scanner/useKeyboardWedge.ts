import { useEffect, useRef } from "react";
import { listenForWedgeScans } from "./keyboardWedge";

/** Deliver codes from a USB / Bluetooth keyboard-wedge reader to `onScan`
 *  while the component is mounted. See `keyboardWedge.ts`. */
export function useKeyboardWedge(onScan: (code: string) => void, enabled = true): void {
  const cb = useRef(onScan);
  cb.current = onScan;
  useEffect(() => {
    if (!enabled) return;
    return listenForWedgeScans(code => cb.current(code));
  }, [enabled]);
}
