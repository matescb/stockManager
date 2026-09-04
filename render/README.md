# render — KiCad render sidecar

Audience: engineer

Draws stored symbols and footprints **exactly as KiCad does**, so the
CAD tab's 2D previews are trustworthy. It wraps `kicad-cli … export svg`
in a tiny stdlib HTTP service (`server.py`) and runs in its own container
(`Dockerfile`) because a full KiCad install has no place in the slim
backend image.

## Why this exists

KiCanvas (the previous in-browser viewer) dropped pins on complex symbols
and crashed on KiCad-9 footprints. The only renderer faithful to KiCad is
KiCad, so 2D previews moved server-side. The 3D previews are unrelated and
stay in the backend (`app/domain/eda/preview3d.py`, cascadio STEP→GLB).

## Contract

The backend (`app/domain/eda/render.py`) is the only client. Over the
internal docker network:

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/healthz` | — | `200 ok` |
| `POST` | `/render/symbol` | a one-symbol `.kicad_sym` library | `image/svg+xml` |
| `POST` | `/render/footprint` | a `.kicad_mod` document | `image/svg+xml` |

The backend wraps a bare stored `(symbol …)` into a one-symbol
`(kicad_symbol_lib …)` before sending; a footprint is already a complete
`(footprint …)` node and goes verbatim. This service never parses either —
it only hands them to kicad-cli. Anything that is not a clean render is a
5xx with a short reason (never the input echoed back), which the backend
maps onto its own 503 "preview unavailable". Nothing is persisted here;
the backend owns the content-addressed SVG cache.

## Operating

- Configured on the backend via `EDA_RENDER_URL` (default
  `http://kicad-render:8080`, set by the compose files).
- **Graceful degrade:** if this container is down the previews return 503,
  never a 500 — the rest of the app is unaffected.
- **Image size:** the base is the official `kicad/kicad:9.0` image
  (non-`full`, so no 3D-model packages). It carries the KiCad GUI runtime
  it does not strictly need; a lean `kicad-cli`-only image is a possible
  follow-up if VPS disk pressure calls for it.

## Bumping the KiCad version

The renderer version is pinned in two places that must move together:

1. `Dockerfile` — the `FROM kicad/kicad:<series>@sha256:<digest>` line
   (refresh command in the Dockerfile comment).
2. `app/domain/eda/render.py` — `KICAD_SERIES`, which is embedded in the
   SVG cache tag so a bump lands every render on a new filename and prunes
   the stale one.
