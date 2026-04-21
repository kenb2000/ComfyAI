"""Configuration helpers for the local planner baseline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


FALCON_DISPLAY_NAME = "Falcon 10B 1.58"
FALCON_MODEL_ID = "tiiuae/Falcon3-10B-Instruct-1.58bit"
FALCON_MODEL_DIRNAME = "Falcon3-10B-Instruct-1.58bit"
DEFAULT_MODEL_ENV_VARS = (
    "COMFYAI_LOCAL_PLANNER_MODEL",
    "COMFYHYBRID_LOCAL_PLANNER_MODEL",
)
DEFAULT_SHARED_STORAGE_CANDIDATES = (
    "/home/ken/Documents/GitHub/BitNet/Falcon3-10B-Instruct-1.58bit",
    f"tools/models/planner/{FALCON_MODEL_DIRNAME}",
)
REQUIRED_MODEL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)
WEIGHT_FILE_GLOBS = (
    "*.safetensors",
    "*.bin",
    "*.gguf",
)


def _resolve_candidate(project_root: Path, raw_path: str | Path | None) -> Path | None:
    if raw_path is None:
        return None
    value = str(raw_path).strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (project_root / path)


def default_local_planner_settings(project_root: Path | str) -> dict[str, Any]:
    project_root_path = Path(project_root)
    repo_model_dir = project_root_path / "tools" / "models" / "planner" / FALCON_MODEL_DIRNAME
    return {
        "enabled": True,
        "mode": "local",
        "platform_target": "linux",
        "display_name": FALCON_DISPLAY_NAME,
        # Falcon is the Linux-first baseline because it is the best
        # throughput/concurrency tradeoff for an always-on helper.
        "default_model_id": FALCON_MODEL_ID,
        "role_mapping": {
            "dispatcher_model": FALCON_MODEL_ID,
            "planner_model": FALCON_MODEL_ID,
            "critic_model": FALCON_MODEL_ID,
        },
        "max_repairs_before_fail": 2,
        "request_timeout_seconds": 30,
        "planner_output_dir": "tools/workspace/planner-output",
        "planner_policy_path": "tools/runtime/planner_policy.json",
        "planner_status_path": "tools/runtime/planner_status.json",
        "model_path": "",
        "expected_storage_dir": str(repo_model_dir.relative_to(project_root_path)).replace("\\", "/"),
        "shared_storage_candidates": list(DEFAULT_SHARED_STORAGE_CANDIDATES),
        "model_path_env_vars": list(DEFAULT_MODEL_ENV_VARS),
        "stronger_model_id": "",
        "escalation_enabled": False,
        "last_verify_at": None,
        "last_verify_ok": None,
        "last_verify_summary": "",
    }


def resolve_planner_output_dir(settings: dict[str, Any], project_root: Path | str) -> Path:
    project_root_path = Path(project_root)
    raw_path = settings.get("planner", {}).get("planner_output_dir", "tools/workspace/planner-output")
    return _resolve_candidate(project_root_path, raw_path) or (project_root_path / "tools" / "workspace" / "planner-output")


def resolve_planner_policy_path(settings: dict[str, Any], project_root: Path | str) -> Path:
    project_root_path = Path(project_root)
    raw_path = settings.get("planner", {}).get("planner_policy_path", "tools/runtime/planner_policy.json")
    return _resolve_candidate(project_root_path, raw_path) or (project_root_path / "tools" / "runtime" / "planner_policy.json")


def resolve_planner_status_path(settings: dict[str, Any], project_root: Path | str) -> Path:
    project_root_path = Path(project_root)
    raw_path = settings.get("planner", {}).get("planner_status_path", "tools/runtime/planner_status.json")
    return _resolve_candidate(project_root_path, raw_path) or (project_root_path / "tools" / "runtime" / "planner_status.json")


def inspect_local_planner_model(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "exists": False,
            "is_dir": False,
            "ok": False,
            "missing_required_files": list(REQUIRED_MODEL_FILES),
            "weight_files": [],
        }

    exists = path.exists()
    is_dir = path.is_dir()
    missing_required = [name for name in REQUIRED_MODEL_FILES if not (path / name).exists()] if exists and is_dir else list(REQUIRED_MODEL_FILES)
    weight_files: list[str] = []
    if exists and is_dir:
        for pattern in WEIGHT_FILE_GLOBS:
            for candidate in sorted(path.glob(pattern)):
                if candidate.is_file():
                    weight_files.append(candidate.name)
    return {
        "exists": exists,
        "is_dir": is_dir,
        "ok": exists and is_dir and not missing_required and bool(weight_files),
        "missing_required_files": missing_required,
        "weight_files": weight_files,
    }


def _candidate_items(settings: dict[str, Any], project_root: Path | str) -> list[tuple[str, Path]]:
    project_root_path = Path(project_root)
    planner_settings = settings.get("planner", {})
    items: list[tuple[str, Path]] = []

    for env_var in list(planner_settings.get("model_path_env_vars", DEFAULT_MODEL_ENV_VARS)):
        env_value = os.environ.get(str(env_var), "").strip()
        resolved = _resolve_candidate(project_root_path, env_value)
        if resolved is not None:
            items.append((f"env:{env_var}", resolved))

    explicit = _resolve_candidate(project_root_path, planner_settings.get("model_path"))
    if explicit is not None:
        items.append(("settings:model_path", explicit))

    for raw_path in list(planner_settings.get("shared_storage_candidates", DEFAULT_SHARED_STORAGE_CANDIDATES)):
        resolved = _resolve_candidate(project_root_path, raw_path)
        if resolved is not None:
            items.append(("settings:shared_storage_candidates", resolved))

    expected = _resolve_candidate(project_root_path, planner_settings.get("expected_storage_dir"))
    if expected is not None:
        items.append(("settings:expected_storage_dir", expected))

    deduped: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in items:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((source, path))
    return deduped


def resolve_local_planner_model_path(settings: dict[str, Any], project_root: Path | str) -> tuple[Path | None, str, dict[str, Any]]:
    first_visible: tuple[Path, str, dict[str, Any]] | None = None
    for source, path in _candidate_items(settings, project_root):
        inspection = inspect_local_planner_model(path)
        if inspection["ok"]:
            return path, source, inspection
        if first_visible is None and inspection["exists"]:
            first_visible = (path, source, inspection)

    if first_visible is not None:
        return first_visible

    candidates = _candidate_items(settings, project_root)
    if candidates:
        source, path = candidates[-1]
        return path, source, inspect_local_planner_model(path)
    return None, "unconfigured", inspect_local_planner_model(None)


def _planner_config_present(planner_settings: dict[str, Any]) -> bool:
    required = (
        "enabled",
        "mode",
        "default_model_id",
        "platform_target",
        "role_mapping",
        "max_repairs_before_fail",
        "request_timeout_seconds",
        "planner_output_dir",
    )
    return all(key in planner_settings for key in required)


def build_local_planner_policy(settings: dict[str, Any], project_root: Path | str) -> dict[str, Any]:
    planner_settings = settings.get("planner", {})
    model_path, model_path_source, inspection = resolve_local_planner_model_path(settings, project_root)
    output_dir = resolve_planner_output_dir(settings, project_root)
    return {
        "enabled": bool(planner_settings.get("enabled", True)),
        "mode": str(planner_settings.get("mode", "local")),
        "platform_target": str(planner_settings.get("platform_target", "linux")),
        "display_name": str(planner_settings.get("display_name", FALCON_DISPLAY_NAME)),
        "default_model_id": str(planner_settings.get("default_model_id", FALCON_MODEL_ID)),
        "role_mapping": dict(planner_settings.get("role_mapping", {})),
        "max_repairs_before_fail": int(planner_settings.get("max_repairs_before_fail", 2)),
        "request_timeout_seconds": float(planner_settings.get("request_timeout_seconds", 30)),
        "planner_output_dir": str(output_dir.resolve()),
        "planner_policy_path": str(resolve_planner_policy_path(settings, project_root).resolve()),
        "planner_status_path": str(resolve_planner_status_path(settings, project_root).resolve()),
        "model_path": str(model_path.resolve()) if model_path is not None else None,
        "model_path_source": model_path_source,
        "model_present": bool(inspection.get("ok", False)),
        "stronger_model_id": str(planner_settings.get("stronger_model_id", "")).strip() or None,
        "escalation_enabled": bool(planner_settings.get("escalation_enabled", False)),
        "last_verify_at": planner_settings.get("last_verify_at"),
        "last_verify_ok": planner_settings.get("last_verify_ok"),
        "last_verify_summary": planner_settings.get("last_verify_summary"),
        "config_present": _planner_config_present(planner_settings),
        "model_inspection": inspection,
    }


def build_local_planner_status(
    settings: dict[str, Any],
    project_root: Path | str,
    *,
    object_info_ok: bool = False,
) -> dict[str, Any]:
    policy = build_local_planner_policy(settings, project_root)
    status = "ready"
    if not policy["config_present"]:
        status = "missing_config"
    elif not policy["model_present"]:
        status = "missing_model"
    elif policy["last_verify_ok"] is False:
        status = "verification_failed"
    elif not object_info_ok:
        status = "waiting_for_comfyui"

    output_dir = Path(policy["planner_output_dir"])
    runtime_healthy = bool(policy["config_present"] and policy["model_present"] and output_dir.exists())
    return {
        **policy,
        "status": status,
        "ready": status == "ready",
        "runtime_healthy": runtime_healthy,
    }


def write_local_planner_policy(settings: dict[str, Any], project_root: Path | str) -> Path:
    path = resolve_planner_policy_path(settings, project_root)
    payload = build_local_planner_policy(settings, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_local_planner_status(settings: dict[str, Any], project_root: Path | str, payload: dict[str, Any]) -> Path:
    path = resolve_planner_status_path(settings, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
