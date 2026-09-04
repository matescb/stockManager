# KiCanvas parsers — test-only bundle

Audience: engineer

A second build of KiCanvas, containing only its `KicadSch` and `KicadPCB`
document parsers. It is imported by exactly one test —
`web/src/components/eda/__tests__/kicanvasContract.test.ts` — and never by
application code. Nothing here ships.

## Why a second bundle exists

The bundle the app actually loads, `web/public/kicanvas/kicanvas.js`,
**exports nothing**. Upstream's entry point (`src/index.ts`) is three
side-effect imports; the file ends by calling
`window.customElements.define(…)` and `document.body.appendChild(…)`. So it
cannot be imported outside a browser and offers no way to reach the
parsers, which is what a contract test has to exercise.

Rebuilding the same commit with a parser-only entry point solves that
without patching anything: same source, same compiler, different entry.

## What it protects

A preview document KiCanvas cannot parse renders **blank** — no exception,
no console error, nothing in Sentry. That failure mode is why the wrapping
in `backend/app/domain/eda/preview.py` is pinned from both ends:

| If this changes | This fails |
|---|---|
| `preview.py`'s builders | `backend/tests/test_eda_preview_fixtures.py` (checked-in documents no longer match) |
| The KiCanvas pin | `kicanvasContract.test.ts` (parsers no longer accept the documents) |

The documents in the middle live at
`backend/tests/fixtures/eda/preview/` and are generated from
`preview.py` — see that backend test for how to refresh them.

## Pin

Built from the **same commit as the shipped bundle**,
`b031159eb74aaa7eef2b026fd85d35bc05ff2095`. Keeping the two in step is not
optional: a contract test that pinned a different revision than the code
under test would pass while production rendered blank. `kicanvasPin.test.ts`
asserts the shipped bundle's sha256 still matches what
`docs/frontend/kicanvas-provenance.md` records, so a bump that touches one and
not the other is caught rather than assumed.

| | |
| --- | --- |
| `index.mjs` sha256 | `ab1db6aba749607ba1e215c3dad9684d40e7661a36d2532262004122561fa982` |
| Size | 126649 bytes |
| License | MIT — `LICENSE.md`, same as the shipped bundle |

## Rebuilding

Do this in the same pass as bumping `web/public/kicanvas/kicanvas.js`, from
a clone of upstream at the new commit:

```bash
cat > parsers-entry.ts <<'EOF'
export { KicadSch } from "./src/kicad/schematic";
export { KicadPCB } from "./src/kicad/board";
EOF

npm ci
./node_modules/.bin/esbuild parsers-entry.ts \
    --bundle --format=esm --platform=neutral --target=es2022 \
    --keep-names --outfile=index.mjs
```

Then copy `index.mjs` here, update the sha256 and size above, and run both
halves of the contract. `index.d.mts` is hand-written and partial — extend
it only as the test needs more.

## Notes

- The bundle imports cleanly in Node, but KiCanvas's logger reaches for
  `window.console` while parsing, so the test defines a minimal `window`
  global first. That is a shim for a browser library running headless, not
  a patch to the library.
- `platform=neutral` rather than `node`: the parsers touch no Node APIs and
  the neutral target keeps the output identical to what a browser would
  get, so the test exercises the same code the app does.
