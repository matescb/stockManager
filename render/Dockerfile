# KiCad render sidecar — kicad-cli behind a tiny stdlib HTTP wrapper.
#
# Draws stored symbols/footprints to SVG for the CAD tab's 2D previews. The
# backend (app/domain/eda/render.py) POSTs entries here over the internal
# docker network; nothing is persisted in this container. See server.py.
#
# Base: the official KiCad 9.0 image (Ubuntu-based; ships `kicad-cli` and
# `python3`). Digest-pinned per INFRA2-015 — the non-`full` tag omits the
# 3D-model library packages the 2D SVG export never touches. Bump the
# digest AND `app/domain/eda/render.py::KICAD_SERIES` together: a version
# change must invalidate the on-disk SVG cache.
#
# To refresh the digest:
#   curl -s "https://hub.docker.com/v2/repositories/kicad/kicad/tags?page_size=50" \
#     | python3 -c "import sys,json;[print(t['name'],t['images'][0]['digest']) for t in json.load(sys.stdin)['results']]"
# Digest pinned on 2026-09-04.
FROM kicad/kicad:10.0@sha256:182c8005cb775a2c448a4c18681d489f1ff472a761885eba3e08b07e3c0564de

# The stored entries never reach disk beyond a per-request scratch dir, so
# the only thing to add is the wrapper. Copied root-owned and world-
# readable; the process only ever reads it.
COPY server.py /app/server.py

# HOME must be writable — kicad-cli writes its config on first run. /tmp is
# writable whatever user the base image runs as, which sidesteps having to
# know that. RENDER_PORT is the port server.py binds.
ENV HOME=/tmp \
    RENDER_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# The base image ships its own desktop entrypoint; clear it so CMD runs the
# sidecar directly rather than as an argument to a KiCad launcher.
ENTRYPOINT []

# Run non-root — kicad-cli parses attacker-supplied geometry, so a parser
# bug should not land as in-container root. The official image already runs
# as uid 1000; pin it numerically so it holds regardless of the base's
# default (HOME is /tmp above, so a uid with no passwd home can still write).
USER 1000

# Readiness for the compose healthcheck — hits the sidecar's own /healthz.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()==b'ok' else 1)"

CMD ["python3", "/app/server.py"]
