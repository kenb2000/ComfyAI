#!/usr/bin/env python3
"""Run ComfyUIhybrid setup flows through the local setup HTTP endpoints."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_layer.ports import allocate_port, default_app_id, default_port_range, record_reservation, write_local_port_state
from prompt_layer.setup_config import DEFAULT_MANIFEST_PATH, DEFAULT_SETTINGS_PATH
from prompt_layer.setup_runtime import benchmark_linux_workstation, verify_setup
from prompt_layer.setup_status import collect_setup_status
from prompt_layer.setup_status_server import make_setup_status_handler


@dataclass
class LocalSetupServer:
    host: str
    port: int
    project_root: Path
    manifest_path: Path
    settings_path: Path
    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None

    def __enter__(self) -> "LocalSetupServer":
        handler = make_setup_status_handler(
            project_root=self.project_root,
            manifest_path=self.manifest_path,
            settings_path=self.settings_path,
        )
        app_id = default_app_id(self.project_root)
        requested_port = int(self.port)
        if requested_port > 0:
            allocation = allocate_port(
                app_id=app_id,
                service_name="setup_flow",
                preferred_port=requested_port,
                host=self.host,
                allowed_range=default_port_range(requested_port),
                pid=os.getpid(),
                notes=str(self.project_root.resolve()),
            )
            bind_port = allocation.assigned_port
        else:
            allocation = None
            bind_port = 0

        self.server = ThreadingHTTPServer((self.host, bind_port), handler)
        record_reservation(
            app_id=app_id,
            service_name="setup_flow",
            protocol="tcp",
            host=self.host,
            requested_port=requested_port,
            assigned_port=self.server.server_port,
            pid=os.getpid(),
            notes=str(self.project_root.resolve()),
        )
        write_local_port_state(app_id=app_id, project_root=self.project_root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"Service setup_flow bound to {self.host}:{self.server.server_port}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.server is not None:
            self.server.shutdown()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.server is not None:
            self.server.server_close()

    @property
    def base_url(self) -> str:
        if self.server is None:
            raise RuntimeError("Local setup server has not started.")
        return f"http://{self.host}:{self.server.server_port}"


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _bool_label(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _request_json(base_url: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 120.0) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


def _stream_ndjson(base_url: str, path: str, payload: dict[str, Any], timeout: float = 3600.0) -> list[dict[str, Any]]:
    req = request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: list[dict[str, Any]] = []
    with request.urlopen(req, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _print_status_summary(status: dict[str, Any], label: str) -> None:
    comfy = status.get("comfyui", {})
    planner = status.get("planner", {})
    models = status.get("optional_models", {})
    planner_state = planner.get("reachable", False)
    planner_label = "PASS" if planner_state else ("WARN" if planner.get("optional", True) else "FAIL")

    _print_section(label)
    print(
        "ComfyUI: "
        f"installed={_bool_label(bool(comfy.get('installed', False)))} "
        f"runnable={_bool_label(bool(comfy.get('runnable', False)))} "
        f"reachable={_bool_label(bool(comfy.get('reachable', False)))} "
        f"url={comfy.get('base_url', '')}",
        flush=True,
    )
    print(
        "Planner: "
        f"{planner_label} "
        f"reachable={planner.get('reachable', False)} "
        f"optional={planner.get('optional', True)} "
        f"url={planner.get('base_url', '')}",
        flush=True,
    )
    print(
        "Optional models: "
        f"present={models.get('present_count', 0)} "
        f"missing={models.get('missing_count', 0)} "
        f"root={models.get('models_root', '')}",
        flush=True,
    )
    print(f"Settings: {status.get('settings_path', '')}", flush=True)


def _describe_acquire_event(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "event"))
    step = str(event.get("step", "")).strip()
    status = str(event.get("status", "")).strip()
    if event_name == "progress":
        downloaded = int(event.get("bytes_downloaded", 0))
        total = event.get("total_bytes")
        if total:
            return f"progress {event.get('model_name', '')}: {downloaded}/{total} bytes"
        return f"progress {event.get('model_name', '')}: {downloaded} bytes"
    if event_name == "error":
        return f"error: {event.get('message', 'unknown error')}"
    if event_name == "complete":
        return f"complete: ok={event.get('ok', False)}"
    details: list[str] = []
    for key in ("action", "message", "reason", "model_name", "label", "target"):
        value = event.get(key)
        if value:
            details.append(f"{key}={value}")
    prefix = step or event_name
    state = status or event_name
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{prefix}: {state}{suffix}"


def _print_acquire_summary(events: list[dict[str, Any]]) -> bool:
    _print_section("Acquire")
    ok = False
    failed = False
    for event in events:
        line = _describe_acquire_event(event)
        print(line, flush=True)
        if event.get("event") == "error":
            failed = True
        if event.get("event") == "complete" and event.get("ok") is True:
            ok = True
    result = ok and not failed
    print(f"ACQUIRE {_bool_label(result)}", flush=True)
    return result


def _collect_verify_failures(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    comfy = result.get("comfyui", {})
    planner = result.get("planner", {})
    health_after = comfy.get("health_after", {})
    object_info = comfy.get("object_info", {})
    planner_after = planner.get("probe_after", {})
    comfy_launch = comfy.get("launch", {})
    comfy_diagnostics = comfy.get("diagnostics", {})

    if not health_after.get("ok", False):
        failures.append(f"ComfyUI health failed: {health_after.get('detail', 'unknown')}")
    if not object_info.get("ok", False):
        failures.append(f"ComfyUI object_info failed: {object_info.get('detail', 'unknown')}")
    if isinstance(comfy_launch, dict) and comfy_launch.get("error"):
        failures.append(f"ComfyUI launch failed: {comfy_launch['error']}")
    if isinstance(comfy_diagnostics, dict) and comfy_diagnostics.get("hint"):
        failures.append(f"ComfyUI launch diagnostic: {comfy_diagnostics['hint']}")
    if not planner_after.get("reachable", False) and not planner.get("optional", True):
        failures.append(f"Planner unreachable: {planner_after.get('detail', 'unknown')}")
    launch_error = planner.get("launch", {})
    if isinstance(launch_error, dict) and launch_error.get("error"):
        failures.append(f"Planner launch failed: {launch_error['error']}")
    return failures


def _print_verify_summary(result: dict[str, Any]) -> bool:
    _print_section("Verify")
    comfy = result.get("comfyui", {})
    planner = result.get("planner", {})
    health_after = comfy.get("health_after", {})
    object_info = comfy.get("object_info", {})
    planner_after = planner.get("probe_after", {})
    planner_required = not planner.get("optional", True)
    planner_ok = planner_after.get("reachable", False) or not planner_required

    print(
        "ComfyUI health: "
        f"{_bool_label(bool(health_after.get('ok', False)))} "
        f"url={comfy.get('health_url', '')}",
        flush=True,
    )
    print(
        "ComfyUI object_info: "
        f"{_bool_label(bool(object_info.get('ok', False)))} "
        f"url={comfy.get('object_info_url', '')}",
        flush=True,
    )
    print(
        "Planner: "
        f"{'PASS' if planner_ok else 'FAIL'} "
        f"reachable={planner_after.get('reachable', False)} "
        f"optional={planner.get('optional', True)} "
        f"url={planner.get('base_url', '')}",
        flush=True,
    )
    if comfy.get("launched_by_verify"):
        launch = comfy.get("launch") or {}
        print(f"ComfyUI sidecar launched: pid={launch.get('pid')}", flush=True)
    if planner.get("launched_by_verify"):
        launch = planner.get("launch") or {}
        print(f"Planner sidecar launched: pid={launch.get('pid')}", flush=True)

    failures = _collect_verify_failures(result)
    if failures:
        for failure in failures:
            print(f"FAIL detail: {failure}", flush=True)
    print(f"VERIFY {_bool_label(bool(result.get('all_required_ok', False)))}", flush=True)
    return bool(result.get("all_required_ok", False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call local ComfyUIhybrid setup endpoints from bootstrap scripts.")
    parser.add_argument("mode", choices=["bootstrap", "verify", "status", "benchmark"], help="Which setup flow to run.")
    parser.add_argument("--host", default="127.0.0.1", help="Local setup server bind host.")
    parser.add_argument("--server-port", type=int, default=0, help="Local setup server bind port. Use 0 for an ephemeral port.")
    parser.add_argument("--project-root", type=Path, default=ROOT, help="Repository root.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Requirements manifest path.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Settings file path.")
    parser.add_argument("--skip-comfyui", action="store_true", help="Skip acquiring the ComfyUI repository during bootstrap.")
    parser.add_argument("--skip-venv", action="store_true", help="Skip venv creation and package installation during bootstrap.")
    parser.add_argument("--acquire-optional-models", action="store_true", help="Acquire optional model files declared in the manifest during bootstrap.")
    parser.add_argument("--no-configure-ports", action="store_true", help="Do not rewrite bind and port settings during bootstrap.")
    parser.add_argument("--no-launch-comfyui", action="store_true", help="Do not launch ComfyUI automatically during verify.")
    parser.add_argument("--no-launch-planner", action="store_true", help="Do not launch the planner sidecar automatically during verify.")
    parser.add_argument("--start-timeout-seconds", type=float, default=45.0, help="Wait time for ComfyUI and planner during verify.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON instead of human-readable summaries.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    manifest_path = args.manifest.resolve()
    settings_path = args.settings.resolve()

    if args.mode == "status":
        status = collect_setup_status(
            manifest_path=manifest_path,
            settings_path=settings_path,
            project_root=project_root,
        )
        if args.json_output:
            print(json.dumps(status), flush=True)
        else:
            _print_status_summary(status, "Status")
        return 0

    if args.mode == "benchmark":
        result = benchmark_linux_workstation(
            payload={
                "launch_comfyui_if_needed": not args.no_launch_comfyui,
                "start_timeout_seconds": args.start_timeout_seconds,
            },
            settings_path=settings_path,
            manifest_path=manifest_path,
            project_root=project_root,
        )
        if args.json_output:
            print(json.dumps(result), flush=True)
        else:
            _print_section("Benchmark")
            print(json.dumps(result, indent=2), flush=True)
        return 0

    if args.mode == "verify" and args.json_output:
        result = verify_setup(
            payload={
                "start_timeout_seconds": args.start_timeout_seconds,
                "launch_comfyui_if_needed": not args.no_launch_comfyui,
                "launch_planner_if_needed": not args.no_launch_planner,
            },
            settings_path=settings_path,
            manifest_path=manifest_path,
            project_root=project_root,
        )
        print(json.dumps(result), flush=True)
        return 0 if result.get("all_required_ok") else 1

    try:
        with LocalSetupServer(
            host=args.host,
            port=args.server_port,
            project_root=project_root,
            manifest_path=manifest_path,
            settings_path=settings_path,
        ) as server:
            print(f"Using setup server at {server.base_url}", flush=True)
            before_status = _request_json(server.base_url, "/setup/status")
            _print_status_summary(before_status, "Status Before")

            acquire_ok = True
            if args.mode == "bootstrap":
                acquire_payload = {
                    "configure_ports": not args.no_configure_ports,
                    "acquire_comfyui": not args.skip_comfyui,
                    "create_venv": not args.skip_venv,
                    "acquire_optional_models": args.acquire_optional_models,
                }
                events = _stream_ndjson(server.base_url, "/setup/acquire", acquire_payload)
                acquire_ok = _print_acquire_summary(events)

            verify_payload = {
                "start_timeout_seconds": args.start_timeout_seconds,
                "launch_comfyui_if_needed": not args.no_launch_comfyui,
                "launch_planner_if_needed": not args.no_launch_planner,
            }
            verify_result = _request_json(
                server.base_url,
                "/setup/verify",
                method="POST",
                payload=verify_payload,
                timeout=max(120.0, args.start_timeout_seconds + 30.0),
            )
            verify_ok = _print_verify_summary(verify_result)

            after_status = _request_json(server.base_url, "/setup/status")
            _print_status_summary(after_status, "Status After")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"FAIL: HTTP error {exc.code} while calling setup endpoints.", flush=True)
        if body:
            print(body, flush=True)
        return 1
    except Exception as exc:
        print(f"FAIL: {exc}", flush=True)
        return 1

    overall_ok = acquire_ok and verify_ok
    print(f"\nFINAL RESULT: {'PASS' if overall_ok else 'FAIL'}", flush=True)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
