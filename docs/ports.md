## Port Allocation

ComfyUIhybrid now uses a deterministic local port allocator for every repo-managed service that binds to a port.

The allocator behavior is:

1. Start from the configured default port.
2. If that port is busy, scan upward through a deterministic local range.
3. Reserve the first free port in the shared master registry.
4. Reuse the previous assigned port for the same service when that reservation is stale and the port is still free.

This lets multiple repos coexist on one machine without hand-editing local ports.

## Master Registry

The shared registry file lives outside any single repo.

- Windows default: `%USERPROFILE%\Projects\MasterPorts.json`
- Linux default: `~/Projects/MasterPorts.json`
- Override: set `MASTER_PORTS_PATH`

Registry format:

```json
{
  "schema_version": 1,
  "updated_at": "2026-04-05T12:00:00+00:00",
  "entries": [
    {
      "app_id": "comfyuihybrid",
      "service_name": "comfyui",
      "protocol": "tcp",
      "host": "127.0.0.1",
      "requested_port": 8188,
      "assigned_port": 8189,
      "pid": 12345,
      "started_at": "2026-04-05T12:00:00+00:00",
      "notes": "C:/Users/Ken/Projects/ComfyUIhybrid"
    }
  ]
}
```

Writes are protected with a lock file and committed with atomic replace semantics so concurrent launches do not corrupt the registry.

## Repo Services

These ComfyUIhybrid entry points now allocate ports safely before binding or spawning:

- `prompt_layer.setup_status_server`
- `scripts/comfyhybrid_setup_flow.py`
- `scripts/launch_comfyui.py`
- `prompt_layer.setup_runtime.launch_comfyui_sidecar`
- `prompt_layer.setup_runtime.launch_planner_sidecar`
- `comfyui/main.py`

The actual assigned port is surfaced through:

- `GET /setup/status`
- `GET /ports/status`
- `GET /health`
- `comfyhybrid-ports`
- `python -m prompt_layer.setup_status_server ports`

Each repo also writes a local snapshot to `tools/runtime/ports.json` for quick inspection.

## Status And Cleanup

Stale reservations are cleaned automatically when the registry is read or updated.

A reservation is treated as stale when:

- the recorded PID is no longer alive
- the port is no longer bound after the short startup grace window
- a newer reservation supersedes the same `app_id + service_name + protocol + host`

To clear stale ports safely:

1. Stop the relevant local services.
2. Run `comfyhybrid-ports` or `python -m prompt_layer.setup_status_server ports`.
3. Confirm the stale entries disappear after the cleanup pass.

If you need to inspect a non-default master registry, set `MASTER_PORTS_PATH` before running the command.

## Smoke Check

Minimal verification flow:

1. Start one service on its default port.
2. Start a second service that normally uses the same default port.
3. Confirm the second service logs `Service <name> bound to <host>:<port>` with the next free port.
4. Check `GET /ports/status` or `comfyhybrid-ports` and verify both assignments are present in `MasterPorts.json`.
5. Stop the second service and start it again while the fallback port remains free.
6. Confirm it reuses the same assigned port.