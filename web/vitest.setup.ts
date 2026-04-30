// vitest 2's default node-environment worker doesn't expose
// globalThis.crypto.subtle (Node has it under crypto.webcrypto). Patch
// globalThis.crypto to point at the WebCrypto bundle so tests of code
// that uses `crypto.subtle.digest` (e.g. bagSignature) run cleanly.
import { webcrypto } from "node:crypto";

if (!globalThis.crypto || !(globalThis.crypto as any).subtle) {
  // @ts-expect-error — assigning Node's webcrypto to the global slot
  globalThis.crypto = webcrypto;
}
