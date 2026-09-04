# ADR-0032: 2D symbol/footprint previews render server-side with kicad-cli, not in-browser

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-04
- **Supersedes**: —
- **Superseded by**: —

## Context

The CAD tab shows a 2D preview of the hosted symbol and footprint a part
uses. The first implementation embedded **KiCanvas**, an in-browser WebGL
viewer, and fed it synthetic wrapper documents (a stored `(symbol …)`
placed in a one-symbol `.kicad_sch`, a stored `(footprint …)` in a
one-footprint `.kicad_pcb`) because KiCanvas reads schematics and boards,
not the `.kicad_sym` / `.kicad_mod` this domain stores.

On the real NSW library that approach failed on exactly the parts that
matter:

- **Symbols** rendered the body outline but dropped **every pin** on any
  non-trivial IC (proven not to be a unit/convert filtering issue —
  flattening the sub-symbols did not help).
- **Footprints** authored by KiCad 9 (`generator_version 9.0`) **crashed**
  KiCanvas's painter outright (`Cannot read properties of undefined
  (reading 'position')`), rendering blank.

KiCanvas is alpha and its pinned commit was already the latest on its
`main`; no newer build, open PR, or fork fixed either defect. KiCad 9
support there is incomplete, and there was nothing to bump to. Simple
passives (a resistor, a capacitor) rendered fine, which is what masked the
gap at first.

The requirement the user set is "render it the same way as KiCad, so I can
see it's correct." Only KiCad renders like KiCad.

## Decision

Render 2D previews **server-side with `kicad-cli`** and serve the result as
SVG.

- A new sidecar container, **`kicad-render`** (`render/`), wraps
  `kicad-cli … export svg` in a tiny stdlib HTTP service. It lives in its
  own image because a KiCad install has no place in the slim backend image
  and the request path must not shell out.
- The backend (`app/domain/eda/render.py`) POSTs the stored entry to the
  sidecar and **caches the SVG content-addressed** at
  `{UPLOAD_DIR}/eda/{ws}/preview/{sha}.{tag}.svg`, next to the 3D GLB cache
  (ADR sibling: `preview3d.py`). The cache tag embeds the pinned KiCad
  series, so a version bump invalidates it. The sidecar is hit once per
  unique entry; every later request is a disk read.
- The routes become `GET /api/eda/{symbols,footprints}/{id}/preview.svg`,
  serving `image/svg+xml`. The frontend renders them through an **`<img>`**
  (`SvgPreview.tsx`), which cannot execute script — the XSS defence for
  SVG generated from attacker-supplied geometry — plus `nosniff`.
- KiCanvas is removed entirely: the vendored bundle, its loader, its
  provenance doc, the synthetic-document builder (`preview.py`), and the
  contract/fixture tests.

The sidecar pins the `kicad/kicad` image (tag 9.0) by digest (INFRA2-015).
KiCad 9 renders the gen-9 parts natively; the non-`full` image omits the
3D-model library packages the 2D export never touches.

## Consequences

- **Fidelity.** Previews are byte-for-byte KiCad output. The AD1938 48-pin
  IC renders with every pin, number and name; the gen-9 footprint that
  crashed KiCanvas renders cleanly.
- **A new moving part.** There is one more container to build and run. It
  is stateless, internal-only, and **not a backend startup dependency**: if
  it is down the previews return `503 eda.preview_unavailable` and the rest
  of the app is unaffected.
- **Image footprint.** The official KiCad image carries the GUI runtime the
  sidecar does not strictly need. Acceptable for correctness now; a lean
  `kicad-cli`-only image is a possible follow-up if VPS disk pressure calls
  for it.
- **Fidelity is proven out of band.** A unit test cannot run kicad-cli, so
  the render tests stub the sidecar and assert the HTTP contract; the true
  output was verified against real parts in the switch's spike, the same
  stance `preview3d.py` takes for GLB.
- **Known limitation unchanged.** Derived `(extends "PARENT")` symbols
  still preview blank when the parent is not stored alongside — no worse
  than before.
