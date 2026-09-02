# tests/fixtures

Audience: engineer

Vendored third-party files the test suite validates against. Nothing here
is ours to edit — a change belongs upstream, and a refresh is a re-fetch.

## `pcm.v1.schema.json`

KiCad's JSON Schema for Plugin & Content Manager metadata. `tests/test_kicad_pcm.py`
validates the LIVE bytes of `repository.json`, `packages.json` and the
`metadata.json` inside the generated package against it.

| | |
|---|---|
| Source | `kicad/pcm/schemas/pcm.v1.schema.json` in <https://gitlab.com/kicad/code/kicad> |
| Raw URL | <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/schemas/pcm.v1.schema.json> |
| Commit | `4a3899d30d63452f514cf337ae14fa09f581b2c1` ("Add PCM v2 schema and content negotiation", 2026-03-05) |
| SHA-256 | `ed07f48d3dceb3af723bba347c6b90d3fc74228b3d73bcb8954850c39f8d9015` |
| Fetched | 2026-09-02 |

Copied byte-for-byte so a future re-fetch diffs cleanly; provenance lives
here rather than in a header comment because JSON has no comments.

**Why v1 and not v2.** v2 loosens several fields into free-form strings —
`license` becomes any string, `type` any lowercase token. v1 closes them:
`license` is a 90-value enum and `type` is `plugin|library|colortheme`. A
document that satisfies v1 satisfies v2, so validating against the
stricter one is what catches a value KiCad would reject. It is also the
schema a KiCad that predates v2 will use, and `kicad_version: "8.0"`
promises those clients work.

This is not hypothetical: `license: "proprietary"` reads as the obvious
label for a private workspace's libraries, is accepted by v2, and is
**not** in v1's enum — so `PLUGIN_CONTENT_MANAGER::ValidateJson` rejected
the entire `packages.json` and the feature silently failed to load. The
enum's catch-all for "no standard licence applies" is `unrestricted`.

## `eda/`

Sample KiCad library files for the phase-2/3 upload and import tests. See
`tests/test_eda_import.py`.
