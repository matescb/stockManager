# ADR-0010: `backend-init` one-shot service chowns `/data`

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

The backend Dockerfile sets `USER appuser` (UID 1000) so the runtime container has no root. The `uploads` named volume mounts at `/data/uploads`; on first creation Docker chowns it to `root:root`. The non-root `appuser` cannot write to it until ownership is fixed.

The previous pattern was a `gosu` trampoline: the backend `command:` started as root, ran `chown -R 1000:1000 /data`, then `exec gosu appuser uvicorn …`. That keeps a root-capable PID 1 in the container for the duration of startup and requires the runtime image to ship `gosu`. It also couples the chown to the backend's startup script, where every change to startup logic risks regressing the privilege drop.

## Decision

A separate one-shot service, `backend-init`, runs as `user: "0:0"` with `command: ["sh","-c","chown -R 1000:1000 /data"]` and `restart: "no"` (`docker-compose.prod.yml:49-58`). It mounts the `uploads` volume, fixes ownership, and exits.

The `backend` service declares:

```
depends_on:
  backend-init:
    condition: service_completed_successfully
```

(`docker-compose.prod.yml:107-111`). Compose waits for `backend-init` to exit cleanly before starting `backend`. The backend image no longer ships `gosu`, and the runtime process is `appuser` from PID 1.

`backend-init` drops all capabilities except `CAP_CHOWN`, with `no-new-privileges:true`.

## Consequences

- **Good**: Runtime container has no root, no `gosu`, no privilege-drop logic in the startup path. The init container has exactly the one capability it needs and exits before serving traffic.
- **Trade-offs**: Two services for what was one. `restart: no` means a pathological state (someone manually `chown`s the volume to a different owner) wouldn't auto-recover; the backend would crash-loop on permission denied until an operator re-runs the init.
- **What it forbids**:
  - Don't reintroduce `gosu` or any other root-trampoline in the backend service `command:`.
  - Don't change the backend Dockerfile away from `USER appuser` (UID 1000) — `backend-init`'s chown target is hard-coded to 1000:1000.
  - Don't drop the `service_completed_successfully` condition; without it the backend may start before the chown finishes and crash on EACCES.
  - Don't remove `restart: "no"` on `backend-init` — a restarting init container would loop forever and `service_completed_successfully` would never trigger.

## Alternatives considered

- **`gosu` trampoline (the previous pattern)** — rejected because it requires a root-capable PID 1 and couples privilege drop to startup logic. See Context.
- **Pre-chowned volume via a Dockerfile `RUN chown` on `/data`** — rejected because named volumes mask whatever the image had at the mountpoint; the chown in the image is overwritten the moment Docker mounts the empty volume on top.

## References

- Source: `docker-compose.prod.yml:49-58` (`backend-init` definition)
- Source: `docker-compose.prod.yml:107-111` (backend `depends_on`)
- Source: `backend/Dockerfile` (`USER appuser`)
- Rule: `CLAUDE.md:137-145`
