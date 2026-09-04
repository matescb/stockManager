# 3D preview test fixtures

`cube.step` is the STEP source `tests/test_eda_preview3d.py` converts to a
GLB. It is a **10 mm axis-aligned cube**, small enough to convert in a few
milliseconds yet a real B-rep solid (not a hand-written stub), so the
conversion exercises the same OpenCASCADE path a vendor STEP would.

## How it was generated

Generated once with `gen_cube.py` (committed beside this file) using
**build123d 0.11.1** (which wraps OpenCASCADE 7.9 via `cadquery-ocp`), then
its `FILE_NAME` timestamp was normalised to `1970-01-01T00:00:00` so the
fixture is byte-reproducible. build123d is **not** a project dependency —
it was installed in a throwaway virtualenv purely to emit this file:

```
python -m venv /tmp/cadgen && /tmp/cadgen/bin/pip install build123d==0.11.1
/tmp/cadgen/bin/python gen_cube.py            # writes cube.step
```

- SHA-256: `d89aeb4b2de9015eb079b6e697318eadbd9c3943a5d2b8c4978e028f28bbc237`
- Size: 15378 bytes
- Schema: AP214 (`AUTOMOTIVE_DESIGN`)

## Licensing

The file is machine-generated geometry (a cube) with no authored content,
produced by our own script. build123d and OpenCASCADE are, respectively,
Apache-2.0 and LGPL-2.1; neither licence reaches a generated output file
like this one.

Other fixtures the test needs (a `#VRML`-headed WRL, a junk STEP) are tiny
and inlined in the test rather than committed here.
