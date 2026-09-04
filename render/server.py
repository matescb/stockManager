#!/usr/bin/env python3
"""KiCad render sidecar — turns a stored library entry into an SVG.

The CAD tab needs symbols and footprints drawn exactly the way KiCad draws
them. The only renderer that is faithful to KiCad is KiCad, so this tiny
HTTP service wraps `kicad-cli … export svg` and nothing else. It lives in
its own container (`render/Dockerfile`) because the KiCad install is large
and has no place in the slim backend image; the backend reaches it over
the internal docker network via `app/domain/eda/render.py`.

Deliberately stdlib-only — no FastAPI, no pip layer. The KiCad image
already ships Python 3, so the sidecar Dockerfile copies this one file and
runs it. Every request is self-contained: write the bytes to a scratch
dir, shell out to `kicad-cli`, read the one SVG it produced, return it,
delete the scratch dir. Nothing is persisted here; the backend owns the
content-addressed cache.

Endpoints
---------
* `GET  /healthz`          → 200 "ok" (compose healthcheck / readiness)
* `POST /render/symbol`    body = a one-symbol `.kicad_sym` library →
                            `image/svg+xml`
* `POST /render/footprint` body = a `.kicad_mod` document →
                            `image/svg+xml`

The backend sends whole documents kicad-cli can open directly: a symbol is
wrapped into a single-symbol `(kicad_symbol_lib …)` before it arrives here,
and a footprint is already a complete `(footprint …)` node. This service
does not parse either — it only ever hands them to kicad-cli — so an entry
that survived the backend's validator renders without a second grammar
here.

Failure stance: anything that is not a clean render is a 5xx with a short
text reason (never the input echoed back), which the backend maps onto its
own "preview unavailable" 503. A malformed request (too big, wrong path)
is a 4xx.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s render-sidecar %(levelname)s %(message)s"
)
_log = logging.getLogger("render-sidecar")

# The kicad-cli binary. Overridable so the image can pin an absolute path
# or a wrapper (e.g. xvfb-run) without editing this file.
KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")

PORT = int(os.environ.get("RENDER_PORT", "8080"))

# Bound the request body. A single symbol library or footprint is a few
# KiB to a couple hundred KiB; the backend caps the stored forms at 1–2
# MiB (storage.py), so 4 MiB here is generous headroom, not a real limit.
MAX_BODY_BYTES = 4 * 1024 * 1024

# kicad-cli is CPU-bound and bounded work, but a pathological input must
# never hang the worker. A cold cache warms one entry per request, so this
# is per-render, not per-batch.
RENDER_TIMEOUT_SECONDS = 30

# Cap the SVG kicad-cli may emit. A pathological but valid footprint can
# amplify into a far larger drawing than its input; refuse to return
# hundreds of MB. The backend also caps what it accepts — this is the
# source-side bound so it never buffers an absurd body.
MAX_SVG_BYTES = 16 * 1024 * 1024

# Each render forks a heavy (~200 MB) kicad-cli process, so bound how many
# run at once: a burst queues on the semaphore instead of fan-forking, and
# a request that waits too long for a slot is told the sidecar is busy.
_MAX_CONCURRENT_RENDERS = int(os.environ.get("RENDER_CONCURRENCY", "4"))
_RENDER_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_RENDERS)
_SLOT_WAIT_SECONDS = 20

# Socket read timeout per connection (ThreadingHTTPServer gives one thread
# each). Without it a client that advertises a large Content-Length and
# then trickles bytes pins a thread forever.
_SOCKET_TIMEOUT_SECONDS = 30

# HOME must be writable — kicad-cli writes its config there on first run.
# The image's home is writable, but set it explicitly so a read-only or
# surprise HOME never turns into a silent render failure.
_KICAD_ENV = {**os.environ, "HOME": os.environ.get("HOME", "/tmp")}


class RenderError(Exception):
    """A render that could not be produced — surfaced to the client as 5xx."""


def _run_kicad_cli(args: list[str], *, cwd: str) -> None:
    """Run `kicad-cli <args>` in `cwd`, raising `RenderError` on any fault."""
    try:
        proc = subprocess.run(
            [KICAD_CLI, *args],
            cwd=cwd,
            env=_KICAD_ENV,
            capture_output=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:  # kicad-cli not on PATH — a build fault
        raise RenderError(f"kicad-cli not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("kicad-cli timed out") from exc
    if proc.returncode != 0:
        # stderr may name the offending token; keep only a short tail and
        # never the input document.
        tail = proc.stderr.decode("utf-8", "replace").strip()[-400:]
        raise RenderError(f"kicad-cli exited {proc.returncode}: {tail}")


def _read_one_svg(out_dir: str, *, prefer: str | None = None) -> bytes:
    """Return the bytes of the single SVG kicad-cli wrote into `out_dir`.

    `kicad-cli sym export svg` names files `<Symbol>_unit<N>.svg` (one per
    unit); a single-unit symbol yields exactly one. `prefer` picks a
    substring (`_unit1`) so a multi-unit symbol previews its first unit
    deterministically rather than whichever the directory listing returns
    first. Footprints yield one `<Footprint>.svg` and pass `prefer=None`.
    """
    svgs = sorted(f for f in os.listdir(out_dir) if f.endswith(".svg"))
    if not svgs:
        raise RenderError("kicad-cli produced no SVG")
    chosen = None
    if prefer is not None:
        chosen = next((f for f in svgs if prefer in f), None)
    chosen = chosen or svgs[0]
    chosen_path = os.path.join(out_dir, chosen)
    if os.path.getsize(chosen_path) > MAX_SVG_BYTES:
        raise RenderError(f"rendered SVG exceeds {MAX_SVG_BYTES} bytes")
    with open(chosen_path, "rb") as handle:
        return handle.read()


def render_symbol(body: bytes) -> bytes:
    """Render a one-symbol `.kicad_sym` library to SVG."""
    with tempfile.TemporaryDirectory(prefix="sym-") as work:
        in_path = os.path.join(work, "in.kicad_sym")
        out_dir = os.path.join(work, "out")
        os.makedirs(out_dir, exist_ok=True)
        with open(in_path, "wb") as handle:
            handle.write(body)
        # No --symbol: the library holds exactly one, so exporting the whole
        # library renders it. Default theme matches the KiCad symbol editor.
        _run_kicad_cli(["sym", "export", "svg", "-o", out_dir, in_path], cwd=work)
        return _read_one_svg(out_dir, prefer="_unit1")


def render_footprint(body: bytes) -> bytes:
    """Render a `.kicad_mod` footprint to SVG."""
    with tempfile.TemporaryDirectory(prefix="fp-") as work:
        # fp export svg reads a `.pretty` directory, not a bare file, so the
        # single stored footprint goes into a throwaway library of one.
        pretty = os.path.join(work, "fp.pretty")
        out_dir = os.path.join(work, "out")
        os.makedirs(pretty, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(pretty, "fp.kicad_mod"), "wb") as handle:
            handle.write(body)
        # No --footprint: the library holds exactly one. Default layers give
        # the copper/silk/fab composite the footprint editor shows.
        _run_kicad_cli(["fp", "export", "svg", "-o", out_dir, pretty], cwd=work)
        return _read_one_svg(out_dir)


_RENDERERS = {"/render/symbol": render_symbol, "/render/footprint": render_footprint}


class Handler(BaseHTTPRequestHandler):
    # Per-connection socket read timeout (see _SOCKET_TIMEOUT_SECONDS).
    timeout = _SOCKET_TIMEOUT_SECONDS

    # Quieter default logging — one line per render at INFO via `_log`.
    def log_message(self, *args) -> None:  # noqa: D401 - stdlib signature
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_text(200, "ok")
        else:
            self._send_text(404, "not found")

    def do_POST(self) -> None:
        renderer = _RENDERERS.get(self.path)
        if renderer is None:
            self._send_text(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_text(400, "bad Content-Length")
            return
        if length <= 0:
            self._send_text(400, "empty body")
            return
        if length > MAX_BODY_BYTES:
            self._send_text(413, "body too large")
            return
        body = self.rfile.read(length)
        # Bound concurrent kicad-cli processes; a burst waits for a slot and
        # a request that waits too long is told the sidecar is busy.
        if not _RENDER_SLOTS.acquire(timeout=_SLOT_WAIT_SECONDS):
            self._send_text(503, "render sidecar busy")
            return
        try:
            svg = renderer(body)
        except RenderError as exc:
            _log.warning("render failed on %s: %s", self.path, exc)
            self._send_text(502, "render failed")
            return
        except Exception:  # noqa: BLE001 - last-resort guard, never 500-with-stack
            _log.exception("unexpected error on %s", self.path)
            self._send_text(500, "internal error")
            return
        finally:
            _RENDER_SLOTS.release()
        self._send(200, svg, "image/svg+xml")


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104 - internal net
    _log.info("listening on :%d (kicad-cli=%s)", PORT, KICAD_CLI)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
