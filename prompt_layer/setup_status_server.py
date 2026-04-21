"""HTTP service exposing setup status, acquire, and verify for ComfyUIhybrid."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .ports import allocate_port, default_app_id, default_port_range, record_reservation, repo_port_status, write_local_port_state
from .planner import LocalPlannerError, LocalPlannerRuntime
from .planner.config import build_local_planner_policy, build_local_planner_status, write_local_planner_policy
from .planner_ui import get_planner_ui_html
from .setup_config import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_SETTINGS_PATH,
    PROJECT_ROOT,
    deep_merge,
    load_settings,
    resolve_comfy_repo_path,
    resolve_workspace_paths,
    save_settings,
)
from .setup_runtime import (
    _probe_json_http,
    acquire_local_planner_runtime,
    benchmark_linux_workstation,
    build_linux_runtime_plan,
    launch_planner_sidecar,
    make_progress_event,
    planner_service_status,
    resolve_planner_base_url,
    run_setup_acquire,
    stop_planner_sidecar,
    verify_local_planner_runtime,
    verify_setup,
    wait_for_http,
    wait_for_unreachable,
)
from .setup_status import collect_setup_status


WORKFLOW_RESULT_KEYS = {
    "workflow",
    "workflow_config",
    "workflow_json",
    "graph",
    "result",
    "payload",
    "data",
    "output",
}


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower())
    normalized = normalized.strip("-")
    return normalized[:48] or "workflow"


def _looks_like_workflow(value: Any) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("nodes"), list) and isinstance(value.get("edges"), list):
            return True
        if value and all(isinstance(key, str) and key.isdigit() for key in value):
            return any(isinstance(item, dict) and ("class_type" in item or "inputs" in item) for item in value.values())
    return False


def _extract_workflow_candidates(event: dict[str, Any]) -> list[tuple[str, Any]]:
    candidates: list[tuple[str, Any]] = []
    visited: set[int] = set()

    def visit(value: Any, label: str, depth: int) -> None:
        if depth > 5:
            return
        if label.endswith("comfy_prompt") or label.endswith("comfyui_prompt"):
            return
        if isinstance(value, (dict, list)):
            marker = id(value)
            if marker in visited:
                return
            visited.add(marker)

        if _looks_like_workflow(value):
            candidates.append((label, value))
            return

        if isinstance(value, str):
            text = value.strip()
            if text[:1] in "{[":
                try:
                    visit(json.loads(text), label, depth + 1)
                except json.JSONDecodeError:
                    return
            return

        if isinstance(value, dict):
            for key, child in value.items():
                next_label = f"{label}.{key}" if label else key
                if key in WORKFLOW_RESULT_KEYS or depth < 2:
                    visit(child, next_label, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:8]):
                visit(child, f"{label}[{index}]", depth + 1)

    visit(event, "event", 0)
    return candidates


def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)


def _save_workflow_snapshot(
    workflow: Any,
    *,
    prompt: str,
    label: str,
    generated_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    generated_dir.mkdir(parents=True, exist_ok=True)
    body = _canonical_json(workflow)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    filename = f"{_now_stamp()}-{_slugify(prompt or label)}-{digest[:10]}.json"
    path = generated_dir / filename
    if not path.exists():
        path.write_text(body, encoding="utf-8")
    relative = str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    return {
        "name": path.name,
        "absolute_path": str(path.resolve()),
        "relative_path": relative,
        "digest": digest,
        "size_bytes": path.stat().st_size,
        "source_label": label,
    }


def _list_saved_workflows(generated_dir: Path, project_root: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if generated_dir.exists():
        for path in sorted(generated_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            relative = str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
            items.append(
                {
                    "name": path.name,
                    "absolute_path": str(path.resolve()),
                    "relative_path": relative,
                    "size_bytes": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
    return {
        "generated_workflows_dir": str(generated_dir.resolve()),
        "count": len(items),
        "items": items,
    }


def _build_helper_payload(
    payload: dict[str, Any],
    *,
    settings: dict[str, Any],
    project_root: Path,
    settings_path: Path,
) -> dict[str, Any]:
    merged = dict(payload)
    workspace_paths = resolve_workspace_paths(settings, project_root)
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    deterministic_paths = dict(merged.get("deterministic_paths", {}))
    deterministic_paths.setdefault("project_root", str(project_root.resolve()))
    deterministic_paths.setdefault("settings_path", str(settings_path.resolve()))
    deterministic_paths.setdefault("workspace_dir", str(workspace_paths["base_dir"].resolve()))
    deterministic_paths.setdefault("generated_workflows_dir", str(workspace_paths["generated_workflows_dir"].resolve()))
    deterministic_paths.setdefault("comfyui_repo_path", str(comfy_root.resolve()))
    deterministic_paths.setdefault("comfyui_models_path", str((comfy_root / "models").resolve()))
    merged["deterministic_paths"] = deterministic_paths
    merged.setdefault("workspace_dir", deterministic_paths["generated_workflows_dir"])
    return merged


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _stringify_summary_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return _compact_json(value)


def _append_unique(items: list[str], candidate: str) -> None:
    text = candidate.strip()
    if text and text not in items:
        items.append(text)


def _coerce_summary_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        lines: list[str] = []
        for item in value[:8]:
            _append_unique(lines, _stringify_summary_value(item))
        return lines
    if isinstance(value, dict):
        lines = []
        for key, item in list(value.items())[:8]:
            _append_unique(lines, f"{key}: {_stringify_summary_value(item)}")
        return lines
    text = _stringify_summary_value(value).strip()
    return [text] if text else []


def _summarize_threshold_lines(value: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in list(value.items())[:8]:
            if _is_scalar(item):
                _append_unique(lines, f"{key}: {_stringify_summary_value(item)}")
            else:
                _append_unique(lines, f"{key}: {_compact_json(item)}")
        return lines
    if isinstance(value, list):
        for item in value[:8]:
            if isinstance(item, dict):
                key = item.get("name") or item.get("threshold") or item.get("metric") or "threshold"
                if "value" in item:
                    _append_unique(lines, f"{key}: {_stringify_summary_value(item.get('value'))}")
                else:
                    _append_unique(lines, f"{key}: {_compact_json(item)}")
            else:
                _append_unique(lines, _stringify_summary_value(item))
        return lines
    return _coerce_summary_lines(value)


def _summarize_tier_mapping_lines(value: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in list(value.items())[:8]:
            if _is_scalar(item):
                _append_unique(lines, f"{key} -> {_stringify_summary_value(item)}")
                continue
            if isinstance(item, dict):
                tier_name = str(item.get("tier") or item.get("name") or item.get("label") or key)
                model_name = item.get("model") or item.get("model_name") or item.get("selected_model") or item.get("target")
                threshold = item.get("threshold") or item.get("thresholds") or item.get("max_tokens") or item.get("prompt_tokens_max")
                parts = [tier_name]
                if model_name is not None:
                    parts.append(f"-> {_stringify_summary_value(model_name)}")
                if threshold is not None:
                    parts.append(f"({_stringify_summary_value(threshold)})")
                _append_unique(lines, " ".join(parts))
                continue
            _append_unique(lines, f"{key}: {_compact_json(item)}")
        return lines

    if isinstance(value, list):
        for index, item in enumerate(value[:8], start=1):
            if isinstance(item, dict):
                tier_name = str(item.get("tier") or item.get("name") or item.get("label") or f"tier {index}")
                model_name = item.get("model") or item.get("model_name") or item.get("selected_model") or item.get("target")
                threshold = item.get("threshold") or item.get("thresholds") or item.get("max_tokens") or item.get("prompt_tokens_max")
                parts = [tier_name]
                if model_name is not None:
                    parts.append(f"-> {_stringify_summary_value(model_name)}")
                if threshold is not None:
                    parts.append(f"({_stringify_summary_value(threshold)})")
                _append_unique(lines, " ".join(parts))
                continue
            _append_unique(lines, _stringify_summary_value(item))
        return lines

    return _coerce_summary_lines(value)


def _find_named_value(value: Any, keys: set[str], depth: int = 0) -> Any | None:
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in keys:
                return child
        for child in value.values():
            found = _find_named_value(child, keys, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value[:8]:
            found = _find_named_value(child, keys, depth + 1)
            if found is not None:
                return found
    return None


def _find_named_scalar(value: Any, keys: set[str], depth: int = 0) -> str | None:
    found = _find_named_value(value, keys, depth=depth)
    if found is None:
        return None
    if _is_scalar(found):
        text = _stringify_summary_value(found).strip()
        return text or None
    return None


def _best_ladder_summary(ladder: Any) -> dict[str, Any]:
    summary = {
        "headline": "",
        "baseline": [],
        "tier_mappings": [],
        "thresholds": [],
    }
    if ladder is None:
        return summary

    if isinstance(ladder, dict) and isinstance(ladder.get("summary"), dict):
        source_summary = ladder.get("summary", {})
        summary["headline"] = str(source_summary.get("headline", "")).strip()
        summary["baseline"] = _coerce_summary_lines(source_summary.get("baseline", []))
        summary["tier_mappings"] = _coerce_summary_lines(
            source_summary.get("tier_mappings", source_summary.get("tiers", []))
        )
        summary["thresholds"] = _coerce_summary_lines(source_summary.get("thresholds", []))
        return summary

    baseline_value = _find_named_value(
        ladder,
        {
            "baseline",
            "baseline_model",
            "baseline_model_id",
            "default_model",
            "default",
            "base_model",
        },
    )
    if baseline_value is not None:
        summary["baseline"] = _coerce_summary_lines(baseline_value)

    tier_value = _find_named_value(
        ladder,
        {
            "tier_mappings",
            "tiers",
            "tier_map",
            "tier_mapping",
            "mapping",
            "mappings",
            "ladder",
        },
    )
    if tier_value is not None:
        summary["tier_mappings"] = _summarize_tier_mapping_lines(tier_value)

    threshold_value = _find_named_value(
        ladder,
        {
            "thresholds",
            "threshold",
            "cutoffs",
            "cutoff",
            "limits",
        },
    )
    if threshold_value is not None:
        summary["thresholds"] = _summarize_threshold_lines(threshold_value)

    if summary["baseline"]:
        summary["headline"] = f"Baseline {summary['baseline'][0]}"
    elif summary["tier_mappings"]:
        summary["headline"] = f"{len(summary['tier_mappings'])} ladder tiers available"
    return summary


def _build_auto_best_ladder_cache(policy: Any) -> dict[str, Any]:
    saved_at = datetime.now(timezone.utc).isoformat()
    if not isinstance(policy, dict):
        return {
            "available": False,
            "saved_at": saved_at,
            "display_timestamp": None,
            "source": "planner_policy",
            "policy_mode": None,
            "summary": {
                "headline": "",
                "baseline": [],
                "tier_mappings": [],
                "thresholds": [],
            },
            "raw": None,
        }

    ladder = policy.get("auto_best_ladder")
    display_timestamp = _find_named_scalar(
        ladder,
        {"saved_at", "updated_at", "generated_at", "timestamp", "created_at"},
    ) if ladder is not None else None
    return {
        "available": ladder is not None,
        "saved_at": saved_at,
        "display_timestamp": display_timestamp or saved_at if ladder is not None else None,
        "source": "planner_policy",
        "policy_mode": policy.get("mode"),
        "summary": _best_ladder_summary(ladder),
        "raw": ladder,
    }


def make_setup_status_handler(
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    settings_path: Path | str | None = None,
):
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH

    class SetupStatusHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _load_settings(self) -> dict[str, Any]:
            return load_settings(
                settings_path=settings_file,
                project_root=project_root_path,
                manifest_path=manifest_file,
            )

        def _planner_runtime(self) -> LocalPlannerRuntime:
            settings = self._load_settings()
            return LocalPlannerRuntime(settings, project_root_path)

        def _save_settings_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
            current = self._load_settings()
            merged = deep_merge(current, patch)
            save_settings(merged, settings_file)
            return merged

        def _persist_local_planner_policy(self) -> dict[str, Any]:
            current = self._load_settings()
            policy = build_local_planner_policy(current, project_root_path)
            write_local_planner_policy(current, project_root_path)
            return policy

        def _write_json(self, status_code: int, payload: Any) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, status_code: int, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw.strip():
                return {}
            return json.loads(raw.decode("utf-8"))

        def _write_ndjson_event(self, payload: dict) -> None:
            line = (json.dumps(payload) + "\n").encode("utf-8")
            self.wfile.write(line)
            self.wfile.flush()

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path in ("/", "/planner/ui"):
                self._write_html(200, get_planner_ui_html())
                return

            if path == "/health":
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "app_id": default_app_id(project_root_path),
                        "ports": repo_port_status(default_app_id(project_root_path)),
                    },
                )
                return

            if path != "/setup/status":
                if path == "/planner/status":
                    status_snapshot = collect_setup_status(
                        manifest_path=manifest_file,
                        settings_path=settings_file,
                        project_root=project_root_path,
                    )
                    self._write_json(200, status_snapshot.get("planner", {}))
                    return

                if path == "/planner/policy":
                    self._write_json(200, self._persist_local_planner_policy())
                    return

                if path == "/planner/models":
                    self._write_json(200, self._planner_runtime().models())
                    return

                if path == "/planner/service/status":
                    settings = self._load_settings()
                    self._write_json(
                        200,
                        planner_service_status(
                            settings,
                            project_root_path,
                            settings_path=settings_file,
                        ),
                    )
                    return

                if path == "/workspace/workflows":
                    settings = self._load_settings()
                    workspace_paths = resolve_workspace_paths(settings, project_root_path)
                    self._write_json(200, _list_saved_workflows(workspace_paths["generated_workflows_dir"], project_root_path))
                    return

                if path == "/ports/status":
                    self._write_json(200, repo_port_status(default_app_id(project_root_path)))
                    return

                self._write_json(404, {"error": "not_found", "path": path})
                return

            payload = collect_setup_status(
                manifest_path=manifest_file,
                settings_path=settings_file,
                project_root=project_root_path,
            )
            self._write_json(200, payload)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._write_json(400, {"error": "invalid_json", "detail": str(exc)})
                return

            if path == "/setup/acquire":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                def emit(event: dict) -> None:
                    self._write_ndjson_event(event)

                try:
                    run_setup_acquire(
                        payload=payload,
                        settings_path=settings_file,
                        manifest_path=manifest_file,
                        project_root=project_root_path,
                        emit=emit,
                    )
                except Exception as exc:
                    emit(make_progress_event("error", action="setup_acquire", message=str(exc)))
                return

            if path == "/setup/verify":
                try:
                    result = verify_setup(
                        payload=payload,
                        settings_path=settings_file,
                        manifest_path=manifest_file,
                        project_root=project_root_path,
                    )
                except Exception as exc:
                    self._write_json(500, {"error": "verify_failed", "detail": str(exc)})
                    return
                self._write_json(200, result)
                return

            if path == "/setup/benchmark":
                try:
                    result = benchmark_linux_workstation(
                        payload=payload,
                        settings_path=settings_file,
                        manifest_path=manifest_file,
                        project_root=project_root_path,
                    )
                except Exception as exc:
                    self._write_json(500, {"error": "benchmark_failed", "detail": str(exc)})
                    return
                self._write_json(200, result)
                return

            if path == "/planner/verify":
                settings = self._load_settings()
                status_snapshot = collect_setup_status(
                    manifest_path=manifest_file,
                    settings_path=settings_file,
                    project_root=project_root_path,
                )
                comfy_snapshot = status_snapshot.get("comfyui", {})
                comfy_base_url = str(comfy_snapshot.get("base_url") or "").strip() or None
                object_info_payload = None
                object_info_url = str(comfy_snapshot.get("object_info_url") or "").strip()
                if object_info_url:
                    _, object_info_payload = _probe_json_http(object_info_url, timeout=2.0)
                try:
                    verify_result = verify_local_planner_runtime(
                        settings,
                        settings_file,
                        project_root_path,
                        object_info_payload=object_info_payload,
                        comfy_base_url=comfy_base_url,
                    )
                except Exception as exc:
                    self._write_json(500, {"error": "planner_verify_failed", "detail": str(exc)})
                    return
                self._write_json(200, verify_result)
                return

            if path == "/planner/rebuild":
                settings = self._load_settings()
                allow_download = bool(payload.get("download_model_if_missing", False))
                try:
                    updated = acquire_local_planner_runtime(
                        settings,
                        settings_file,
                        project_root_path,
                        emit=lambda event: None,
                        allow_download=allow_download,
                    )
                except Exception as exc:
                    self._write_json(500, {"error": "planner_rebuild_failed", "detail": str(exc)})
                    return
                runtime = LocalPlannerRuntime(updated, project_root_path)
                status_snapshot = collect_setup_status(
                    manifest_path=manifest_file,
                    settings_path=settings_file,
                    project_root=project_root_path,
                )
                self._write_json(
                    200,
                    {
                        "ok": bool(status_snapshot.get("planner", {}).get("model_present")),
                        "planner": status_snapshot.get("planner", {}),
                        "policy": runtime.policy(),
                    },
                )
                return

            if path == "/planner/policy":
                planner_patch = {}
                for key in (
                    "enabled",
                    "mode",
                    "request_timeout_seconds",
                    "max_repairs_before_fail",
                    "stronger_model_id",
                    "escalation_enabled",
                ):
                    if key in payload:
                        planner_patch[key] = payload[key]
                if planner_patch:
                    settings = self._save_settings_patch({"planner": planner_patch})
                else:
                    settings = self._load_settings()
                self._write_json(200, build_local_planner_policy(settings, project_root_path))
                return

            if path == "/planner/research/run":
                self._write_json(
                    409,
                    {
                        "error": "escalation_disabled",
                        "detail": "Research mode is not part of the Linux-first Falcon baseline yet.",
                    },
                )
                return

            if path == "/planner/service/config":
                planner_patch: dict[str, Any] = {}
                if "assistant_repo_path" in payload:
                    planner_patch["assistant_repo_path"] = str(payload.get("assistant_repo_path", "")).strip()
                if "base_url" in payload:
                    planner_patch["base_url"] = str(payload.get("base_url", "")).strip()
                if "health_endpoint" in payload:
                    planner_patch["health_endpoint"] = str(payload.get("health_endpoint", "")).strip() or "/health"
                if "can_launch_as_sidecar" in payload:
                    planner_patch["can_launch_as_sidecar"] = bool(payload.get("can_launch_as_sidecar"))
                    planner_patch.setdefault("sidecar_launch", {})["enabled"] = bool(payload.get("can_launch_as_sidecar"))

                settings = self._save_settings_patch({"planner": planner_patch}) if planner_patch else self._load_settings()
                self._write_json(
                    200,
                    planner_service_status(
                        settings,
                        project_root_path,
                        settings_path=settings_file,
                    ),
                )
                return

            if path == "/planner/service/start":
                if "assistant_repo_path" in payload:
                    settings = self._save_settings_patch({"planner": {"assistant_repo_path": str(payload.get("assistant_repo_path", "")).strip()}})
                else:
                    settings = self._load_settings()

                status_before = planner_service_status(settings, project_root_path, settings_path=settings_file)
                if status_before["healthy"]:
                    self._write_json(200, {"ok": True, "already_running": True, "status": status_before})
                    return

                try:
                    launch = launch_planner_sidecar(settings, project_root_path)
                except Exception as exc:
                    self._write_json(400, {"ok": False, "error": "planner_start_failed", "detail": str(exc), "status": status_before})
                    return

                timeout_seconds = float(payload.get("start_timeout_seconds", 45))
                health_after = wait_for_http(launch.get("health_url", status_before["health_url"]), timeout=timeout_seconds, require_ok=True)
                status_after = planner_service_status(settings, project_root_path, settings_path=settings_file)
                self._write_json(
                    200,
                    {
                        "ok": bool(health_after.get("ok", False)),
                        "launch": launch,
                        "health_after": health_after,
                        "status": status_after,
                    },
                )
                return

            if path == "/planner/service/stop":
                settings = self._load_settings()
                stop_result = stop_planner_sidecar(settings, project_root_path)
                status_before = planner_service_status(settings, project_root_path, settings_path=settings_file)
                health_after = wait_for_unreachable(status_before["health_url"], timeout=float(payload.get("stop_timeout_seconds", 15)))
                status_after = planner_service_status(settings, project_root_path, settings_path=settings_file)
                self._write_json(
                    200,
                    {
                        "ok": not health_after.get("reachable", False),
                        "stop": stop_result,
                        "health_after": health_after,
                        "status": status_after,
                    },
                )
                return

            if path == "/helper/process":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                settings = self._load_settings()
                helper_payload = _build_helper_payload(
                    payload,
                    settings=settings,
                    project_root=project_root_path,
                    settings_path=settings_file,
                )
                status_snapshot = collect_setup_status(
                    manifest_path=manifest_file,
                    settings_path=settings_file,
                    project_root=project_root_path,
                )
                linux_state = status_snapshot.get("linux_workstation", {})
                runtime_request = {
                    "profile": payload.get("workflow_profile") or payload.get("profile") or "preview",
                    "width": payload.get("width"),
                    "height": payload.get("height"),
                    "frames": payload.get("frames"),
                    "fps": payload.get("fps"),
                    "variant": payload.get("variant") or payload.get("model_variant"),
                    "low_workstation_impact": payload.get("low_workstation_impact", True),
                    "weight_streaming": payload.get("weight_streaming"),
                }
                linux_runtime_plan = build_linux_runtime_plan(settings, linux_state, runtime_request)
                helper_payload["linux_workstation"] = {
                    "role": linux_state.get("role"),
                    "role_label": linux_state.get("role_label"),
                    "active_profile": linux_state.get("active_profile"),
                    "runtime_plan": linux_runtime_plan,
                    "recommended_config": {
                        "workflow_profile": linux_runtime_plan.get("effective_profile"),
                        "model_variant": (linux_runtime_plan.get("selected_model_variant") or {}).get("label"),
                        "async_offload": (linux_runtime_plan.get("optimizations") or {}).get("async_offload"),
                        "pinned_memory": (linux_runtime_plan.get("optimizations") or {}).get("pinned_memory"),
                        "weight_streaming": (linux_runtime_plan.get("optimizations") or {}).get("weight_streaming"),
                    },
                }
                helper_payload["runtime_preferences"] = {
                    "workflow_profile": linux_runtime_plan.get("effective_profile"),
                    "model_variant": (linux_runtime_plan.get("selected_model_variant") or {}).get("label"),
                    "low_workstation_impact": linux_runtime_plan.get("low_workstation_impact"),
                    "async_offload": (linux_runtime_plan.get("optimizations") or {}).get("async_offload"),
                    "pinned_memory": (linux_runtime_plan.get("optimizations") or {}).get("pinned_memory"),
                    "weight_streaming": (linux_runtime_plan.get("optimizations") or {}).get("weight_streaming"),
                    "nvfp4": False,
                }
                workspace_paths = resolve_workspace_paths(settings, project_root_path)
                prompt = str(helper_payload.get("prompt") or helper_payload.get("request") or "workflow").strip()
                seen_digests: set[str] = set()
                planner_runtime = LocalPlannerRuntime(settings, project_root_path)
                comfy_snapshot = status_snapshot.get("comfyui", {})
                object_info_payload = None
                object_info_url = str(comfy_snapshot.get("object_info_url") or "").strip()
                if object_info_url:
                    _, object_info_payload = _probe_json_http(object_info_url, timeout=2.0)

                try:
                    self._write_ndjson_event({"event": "linux_runtime_plan", **linux_runtime_plan})
                    for warning in linux_runtime_plan.get("warnings", []):
                        self._write_ndjson_event({"event": "warning", "message": warning, "scope": "linux_runtime_plan"})
                    plan_result = planner_runtime.plan(
                        helper_payload,
                        object_info_payload=object_info_payload,
                        comfy_base_url=str(comfy_snapshot.get("base_url") or "").strip() or None,
                        emit=self._write_ndjson_event,
                    )
                    for label, workflow in _extract_workflow_candidates(plan_result):
                        digest = hashlib.sha256(_canonical_json(workflow).encode("utf-8")).hexdigest()
                        if digest in seen_digests:
                            continue
                        seen_digests.add(digest)
                        saved = _save_workflow_snapshot(
                            workflow,
                            prompt=prompt,
                            label=label,
                            generated_dir=workspace_paths["generated_workflows_dir"],
                            project_root=project_root_path,
                        )
                        self._write_ndjson_event({"event": "workflow_saved", **saved})
                except LocalPlannerError as exc:
                    self._write_ndjson_event({"type": "error", "event": "error", "data": {"message": str(exc)}})
                except Exception as exc:
                    self._write_ndjson_event({"type": "error", "event": "error", "data": {"message": str(exc)}})
                return

            self._write_json(404, {"error": "not_found", "path": path})

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return SetupStatusHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve ComfyUIhybrid setup endpoints.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for the setup server.")
    parser.add_argument("--port", type=int, default=8010, help="Bind port for the setup server.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to requirements manifest JSON.",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help="Path to ComfyUIhybrid settings.json.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project root used to resolve runtime paths.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ports":
        parser = argparse.ArgumentParser(description="Show ComfyUIhybrid port assignments.")
        parser.add_argument(
            "--project-root",
            type=Path,
            default=PROJECT_ROOT,
            help="Project root used to derive the repo app id.",
        )
        args = parser.parse_args(argv[1:])
        payload = repo_port_status(default_app_id(args.project_root))
        print(json.dumps(payload, indent=2))
        return 0

    args = build_parser().parse_args(argv)
    handler = make_setup_status_handler(
        project_root=args.project_root,
        manifest_path=args.manifest,
        settings_path=args.settings,
    )
    app_id = default_app_id(args.project_root)
    allocation = allocate_port(
        app_id=app_id,
        service_name="setup_status",
        preferred_port=args.port,
        host=args.host,
        allowed_range=default_port_range(args.port),
        pid=os.getpid(),
        notes=str(args.project_root.resolve()),
    )
    server = ThreadingHTTPServer((args.host, allocation.assigned_port), handler)
    record_reservation(
        app_id=app_id,
        service_name="setup_status",
        protocol="tcp",
        host=args.host,
        requested_port=args.port,
        assigned_port=server.server_port,
        pid=os.getpid(),
        notes=str(args.project_root.resolve()),
    )
    write_local_port_state(app_id=app_id, project_root=args.project_root)
    print(
        f"Service setup_status bound to {args.host}:{server.server_port}\n"
        "Serving ComfyUIhybrid setup endpoints on "
        f"http://{args.host}:{server.server_port}/planner/ui, "
        f"http://{args.host}:{server.server_port}/setup/status, "
        f"http://{args.host}:{server.server_port}/setup/acquire, "
        f"http://{args.host}:{server.server_port}/setup/verify, "
        f"http://{args.host}:{server.server_port}/setup/benchmark, "
        f"http://{args.host}:{server.server_port}/ports/status, "
        f"http://{args.host}:{server.server_port}/health"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def ports_main(argv: list[str] | None = None) -> int:
    extra_args = list(sys.argv[1:] if argv is None else argv)
    return main(["ports", *extra_args])


if __name__ == "__main__":
    raise SystemExit(main())
