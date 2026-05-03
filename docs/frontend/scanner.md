# Scanner

Audience: engineer

The barcode scanner pipeline: dispatcher (`Scanner.tsx`), the two backends
(ZXing-wasm and Scandit), the bag-code parser (`bagCode.ts`), and the
SHA-256 signature that's the only stable correlation key for re-scanned
bags. The signature itself is a CLAUDE.md hard invariant; this page
covers the FE side.

## Dispatcher

`web/src/components/scanner/Scanner.tsx`. Reads the workspace's chosen
scanner backend from `/api/workspaces/current` and lazy-mounts only the
chosen decoder. Each backend pulls a multi-MB wasm blob, so splitting
them keeps the SPA shell small.

```tsx
// web/src/components/scanner/Scanner.tsx:43-93
const { data: ws, isLoading } = useQuery({
  queryKey: useWsKey("ws", "current"),
  queryFn: () => api.get<WsScanner>("/workspaces/current"),
});
// …
if (ws.scanner === "scandit") {
  if (!ws.has_scanner_license_key) {
    return <ScanditPlaceholder />;       // settings nudge
  }
  return <ScanditScannerWithKey … />;
}
return <ZxingScanner … />;
```

Both backends conform to the same `Props` shape so call sites
(`MpnLookup`, `ScanImport`) don't change (`Scanner.tsx:25-35`).

```ts
// web/src/components/scanner/Scanner.tsx:20-23
export type ScanResult = { data: string; symbology: string };
const LICENSED_SYMBOLOGIES = ["Code128", "Code39", "QR", "DataMatrix", "PDF417"] as const;
export type LicensedSymbology = (typeof LICENSED_SYMBOLOGIES)[number];
```

`symbologies?` restricts the recognizer — useful when a page only wants
the 2D Data Matrix on a distributor bag (the 1D Code128 alongside carries
single unstructured fields that decode to junk MPNs)
(`Scanner.tsx:27-34`).

### Scandit license key handling

The license key is workspace-scoped and never echoes through the regular
`/workspaces/current` shape. `ScanditScannerWithKey`
(`Scanner.tsx:101-130`) fetches it from a dedicated endpoint
`/workspaces/current/scanner-license-key` so the key stays scoped to
the scanner mount:

```tsx
// web/src/components/scanner/Scanner.tsx:110-113
const { data, isLoading, error } = useQuery({
  queryKey: useWsKey("ws", "scanner", "license-key"),
  queryFn: () => api.get<{ license_key: string }>("/workspaces/current/scanner-license-key"),
});
```

## ZxingScanner

`web/src/components/scanner/ZxingScanner.tsx`. Default backend so workspaces
without a Scandit license still get a working `/parts/scan*` flow.

### Decoder loop

```
<video>  ← getUserMedia
  │
  ▼ requestAnimationFrame loop, throttled to ~6 Hz (SCAN_PERIOD_MS = 160)
offscreen <canvas>.drawImage(video, srcRect…) → ctx.getImageData
  │
  ▼
readBarcodes(imageData, { formats: [...] })
  │
  ▼ debounced 1.2s against the previous payload (DUPLICATE_WINDOW_MS)
onScan({ data, symbology })
```

Schematic at `ZxingScanner.tsx:9-23`. The video preview is rendered by us
(not by the wasm SDK) because we need pixel access to the current frame
anyway and a visible preview is what makes hand-aiming feasible
(`ZxingScanner.tsx:22-23`).

### Wasm load

`prepareZXingModule` is called with a `locateFile` override that points
at `/zxing/zxing_reader.wasm` (copied there by
`scripts/copy-zxing-wasm.mjs` as a prebuild step) instead of the default
jsDelivr CDN URL (`ZxingScanner.tsx:185-191`). Idempotent across
renders — `zxing-wasm` caches the module promise internally.

### Camera + zoom UX

Phones expose multiple cameras + per-track hardware zoom; PC webcams
usually expose neither. The component handles both
(`ZxingScanner.tsx:25-37`):

- Picker visible whenever `enumerateDevices()` returns >1 video input,
  cached in localStorage under `scanner.deviceId`
  (`ZxingScanner.tsx:60`, `:392-398`).
- Zoom slider is **always** visible whenever the camera is up. If
  `track.getCapabilities().zoom` reports a range, drives the hardware
  via `applyConstraints({ advanced: [{ zoom }] })` (`ZxingScanner.tsx:381-389`).
  Otherwise crops the source rect in software ("digital zoom"), capped
  at 4× — past that the decoder is fed visibly-pixelated input
  (`ZxingScanner.tsx:99-101`).
- In digital mode the preview gets a CSS `scale()` so the user sees
  the same crop the decoder sees (`ZxingScanner.tsx:401-403`).

### Closure-staleness pattern

`onScan` and `symbologies` flow through refs so a parent re-render with
fresh closures doesn't tear down the camera (FE CRIT-2 / MED-9):

```ts
// web/src/components/scanner/ZxingScanner.tsx:126-129
const onScanRef = useRef(onScan);
useEffect(() => { onScanRef.current = onScan; }, [onScan]);
const symbologiesRef = useRef(symbologies);
useEffect(() => { symbologiesRef.current = symbologies; }, [symbologies]);
```

The decoder loop reads `onScanRef.current(...)` at hit time
(`ZxingScanner.tsx:312`). Without this, every parent re-render restarted
the camera and reloaded the wasm — visible camera stutter or full
soft-lock under any state churn.

The dep array on the camera effect is intentionally
`[deviceId, retryToken]` only (`ZxingScanner.tsx:373`).

### Error UX

DOMException names map to user-friendly states (`ZxingScanner.tsx:324-353`):

- `NotAllowedError` / `PermissionDeniedError` → permission panel with
  "Try again" button that bumps `retryToken` to re-prompt without a full
  reload.
- `NotFoundError` / `DevicesNotFoundError` → "No camera was found".
- `NotReadableError` / `TrackStartError` → "Camera is in use by another
  app" + same retry affordance.
- Anything else → raw message.

`OverconstrainedError` triggered by a stale saved `deviceId` falls back
to the default camera and clears the preference
(`ZxingScanner.tsx:206-216`).

### Audible + haptic feedback

`scanFeedback()` at `ZxingScanner.tsx:67-97` — short 880Hz square
WebAudio tone with an 80ms exponential decay envelope, plus a 50ms
`navigator.vibrate(50)`. Both are best-effort. A single `AudioContext`
is reused across beeps because Chrome caps the count per tab
(`ZxingScanner.tsx:74-76`). Scandit's SDK does feedback for free; with
the open-source decoder we wire it ourselves.

## ScanditScanner

`web/src/components/scanner/ScanditScanner.tsx`. Opt-in commercial
backend. Workspaces without a license key see the placeholder rendered
by the dispatcher instead of mounting this component, so the SDK is
never called without a key (`ScanditScanner.tsx:20-22`).

The license is bound to specific origins by Scandit; the SDK rejects
domains the key wasn't provisioned for.

Same closure-staleness ref pattern as ZXing
(`ScanditScanner.tsx:51-54`):

```ts
// web/src/components/scanner/ScanditScanner.tsx:51-54
const onScanRef = useRef(onScan);
useEffect(() => { onScanRef.current = onScan; }, [onScan]);
const symbologiesRef = useRef(symbologies);
useEffect(() => { symbologiesRef.current = symbologies; }, [symbologies]);
```

Init flow (`ScanditScanner.tsx:56-126`):

1. `DataCaptureContext.forLicenseKey(licenseKey, { libraryLocation: "/scandit/" })`
   — same self-hosted-wasm pattern as ZXing.
2. `Camera.pickBestGuess()` → `setMirrorImageEnabled(false)` →
   `ctx.setFrameSource(camera)`.
3. Build `BarcodeCaptureSettings`, enable the requested symbologies via
   `Symbology[key]` (`ScanditScanner.tsx:88-97`).
4. `BarcodeCapture.forContext(...).addListener({ didScan: (_, session) => onScanRef.current(session.newlyRecognizedBarcode) })`
   (`ScanditScanner.tsx:98-106`).
5. Mount `DataCaptureView` + overlay.
6. `camera.switchToDesiredState(FrameSourceState.On)`.

The init effect's only dep is `[licenseKey]` (`ScanditScanner.tsx:135`).
Failures during init bubble to Sentry so ops sees recurring init
failures (license expiry, libraryLocation misconfig, etc.) without
depending on someone watching the browser console
(`ScanditScanner.tsx:115-122`).

### Symbology change requires remount

`symbologies` is read at SDK-init time only (`ScanditScanner.tsx:84-97`).
A parent that needs to change which symbologies are decoded must remount
the component (key it by symbologies). No caller does this today.

## `bagCode.ts` — parser for Mouser / DigiKey 2D bags

`web/src/lib/bagCode.ts`. The 2D Data Matrix codes printed on component
bags follow MIL-STD-130N / ANSI MH10.8.2: `[)>` header + separator-delimited
records, each prefixed with a Data Identifier code
(`bagCode.ts:1-31`).

Supported DIs (`bagCode.ts:5-16`):

| DI | Field |
|---|---|
| `1P` | Manufacturer part number (the field we actually want) |
| `30P` | Alternate manufacturer P/N (DigiKey) |
| `P` | Distributor part number |
| `Q` | Quantity |
| `1V` | Manufacturer name |
| `10D` / `9D` | Date code (YYWW) |
| `1T` | Lot / batch |
| `1S` | Serial number |
| `K` | Customer reference |
| `1K` | Purchase order number |
| `14K` | Line item |
| `11K` | Invoice ref |

`4L`, `13Z`, `T` are recognized but not surfaced (`bagCode.ts:253-256`).

### Three-pass parse

`parseBagCode(raw)` (`bagCode.ts:189-265`):

1. **Split on real or substitute separators**
   (`SEPARATOR_RE = /\{GS\}|<FNC1>|[\x1c\x1d\x1e\x1f\x04]/g`,
   `bagCode.ts:91`). Includes ASCII control chars (FS, GS, RS, US, EOT)
   plus the literal placeholders some libraries write.
2. **If only one chunk emerged, fall back to inline DI scanning**
   (`inlineSplit`, `bagCode.ts:142-157`) — regex-find known DI prefixes
   anchored at start-of-string or after a non-alphanumeric char.
3. **If no DI was ever found and there's no `[)>` header**, treat the
   whole input as a plain MPN (1D Code128 case)
   (`bagCode.ts:205-208`).

Why printable separators like `#`, `]`, `^` are deliberately excluded
from the splitter: they occur as legitimate content in distributor
codes (Mouser's customer-reference field commonly contains `#`, e.g.
`K#44861 A #44920`, see `bagCode.ts:84-90`). When a scanner munges the
real separators, the inline-DI fallback recovers without false splits.

### Control Picture normalisation

ZXing-wasm in HRI text mode emits the "Control Pictures" Unicode block
(U+2400..U+243F) instead of raw ASCII control bytes — a real GS
(0x1D) comes through as U+241D (`␝`) which renders as a printable
character and is invisible to `SEPARATOR_RE`. Without the fix, field
separators land inside the value half of each DI and we send an MPN
like `98266-0897␝` to the provider lookup
(`bagCode.ts:93-105`).

`normalizeControlPictures` (`bagCode.ts:106-117`) maps them back to
ASCII before any other parsing step:

```ts
// web/src/lib/bagCode.ts:106-117
function normalizeControlPictures(s: string): string {
  return s
    .replace(/␄/g, "\x04")
    .replace(/␜/g, "\x1c")
    .replace(/␝/g, "\x1d")
    .replace(/␞/g, "\x1e")
    .replace(/␟/g, "\x1f")
    .replace(/␠/g, " ");                  // U+2420 (Symbol for Space)
}
```

The space substitution matters because `customerRef` carries literal
spaces; without it the parsed field is `#44861␠A␠#44920` in the UI
(`bagCode.ts:113-116`, regression test at
`web/src/lib/bagCode.test.ts:38-45`).

### `bagSignature(raw)`

`bagCode.ts:177-187`. SHA-256 hash of the **normalised** raw bag code,
used to recognise the same physical bag scanned twice
(CLAUDE.md hard invariant — `bag_signature` on `stock_entries`):

```ts
// web/src/lib/bagCode.ts:177-187
export async function bagSignature(raw: string): Promise<string | null> {
  const normalised = normalizeControlPictures((raw ?? "").trim());
  if (!normalised) return null;
  if (!globalThis.crypto?.subtle) return null;
  const bytes = new TextEncoder().encode(normalised);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}
```

The normalisation step before hashing is load-bearing: a code decoded
by ZXing (with U+241D etc.) hashes identically to the same code decoded
by Scandit (with raw 0x1d etc.). If you touch this file, keep the
normalisation order the same — the signature is the only stable
correlation key (CLAUDE.md hard invariant).

Returns `null` on empty input or absent `crypto.subtle` (legacy stacks)
— the caller shouldn't dedup on an empty signature, that would block
every restock (`bagCode.ts:175-181`).

The vitest setup (`web/vitest.setup.ts`) patches `globalThis.crypto`
with Node's `webcrypto` so tests of code using `crypto.subtle.digest`
run cleanly. See [testing](testing.md).

### Synthesised lot / comment helpers

`bagLotName(b)` (`bagCode.ts:62-67`) and `bagComments(b)`
(`bagCode.ts:74-81`) build human-readable strings from the parsed
fields. Lot name combines `1T` lot/batch + `10D` date code; comments
combine `K` customer ref + `1K` PO + `14K` line + `11K` invoice. Both
return `null` when the relevant fields are absent — no synthetic lot
row should be created in that case.

### Test fixtures

`web/src/lib/bagCode.test.ts` — every test corresponds to a regression
that hit users in production:

- Real Scandit-shaped input with raw control bytes (line 12)
- ZXing Control Pictures normalisation (line 23) — the U+241D regression
  that 500'd `lookup-mpn`
- Symbol-for-Space inside customerRef (line 38)
- Plain MPN with no DI / header → treated as MPN itself (line 47)

`web/src/lib/__fixtures__/bagSignatures.json` carries known-good signatures
for cross-decoder hash equivalence.
