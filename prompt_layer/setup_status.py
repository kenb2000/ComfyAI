"""Manifest-backed setup inspection for ComfyUIhybrid."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .ports import default_app_id, repo_port_status, resolve_registered_port
from .setup_config import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_SETTINGS_PATH,
    PROJECT_ROOT,
    load_requirements_manifest,
    load_settings,
    resolve_assistant_repo_path,
    resolve_comfy_repo_path,
    resolve_path,
    resolve_python_executable,
    resolve_tool_paths,
    resolve_workspace_paths,
)
from .setup_runtime import _probe_http
from .setup_runtime import describe_service_port
from .setup_runtime import planner_health_url
from .setup_runtime import resolve_planner_base_url
from .setup_runtime import split_base_url_host_port


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_model_source(item: dict[str, Any]) -> tuple[str, str]:
    source = item.get("source", "")
    if isinstance(source, dict):
        return str(source.get("kind", "")).strip(), str(source.get("value", "")).strip()
    return str(source).strip(), str(item.get("source_value", "")).strip()


def _collect_optional_models_status(
    manifest: dict[str, Any],
    project_root: Path,
    comfy_root: Path,
) -> dict[str, Any]:
    models_root = comfy_root / "models"
    items: list[dict[str, Any]] = []
    present_count = 0
    missing_count = 0

    for item in manifest.get("optional_comfy_models", []):
        target_rel = Path(item["target_path_relative_to_models"])
        target_abs = models_root / target_rel
        size_min = int(item.get("expected_size_min_bytes", 0))
        expected_sha256 = item.get("sha256")
        source_kind, source_value = _parse_model_source(item)

        source_abs: str | None = None
        source_exists: bool | None = None
        if source_kind == "local_path" and source_value:
            source_path = resolve_path(project_root, source_value)
            if source_path is not None:
                source_abs = str(source_path.resolve())
                source_exists = source_path.exists()

        status = "missing"
        size_bytes: int | None = None
        sha256_match: bool | None = None

        if target_abs.exists() and target_abs.is_file():
            size_bytes = target_abs.stat().st_size
            if size_bytes < size_min:
                status = "invalid_size"
            elif expected_sha256:
                sha256_match = _sha256_for_file(target_abs).lower() == str(expected_sha256).lower()
                status = "present" if sha256_match else "invalid_sha256"
            else:
                status = "present"

        if status == "present":
            present_count += 1
        else:
            missing_count += 1

        items.append(
            {
                "model_name": item["model_name"],
                "type": item["type"],
                "status": status,
                "target_path_relative_to_models": str(target_rel).replace("\\", "/"),
                "target_absolute_path": str(target_abs.resolve()),
                "size_bytes": size_bytes,
                "expected_size_min_bytes": size_min,
                "sha256_configured": bool(expected_sha256),
                "sha256_match": sha256_match,
                "source": {
                    "kind": source_kind,
                    "value": source_value,
                    "resolved_absolute_path": source_abs,
                    "exists": source_exists,
                },
            }
        )

    return {
        "models_root": str(models_root.resolve()),
        "present_count": present_count,
        "missing_count": missing_count,
        "items": items,
    }


def collect_setup_status(
    manifest_path: Path | str | None = None,
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    manifest = load_requirements_manifest(manifest_file)
    settings = load_settings(settings_path=settings_file, project_root=project_root_path, manifest_path=manifest_file)

    comfy_settings = settings.get("comfyui", {})
    planner_settings = settings.get("planner", {})
    comfy_root = resolve_comfy_repo_path(settings, project_root_path)
    comfy_main = comfy_root / "main.py"
    python_executable = resolve_python_executable(settings, project_root_path)
    tool_paths = resolve_tool_paths(settings, project_root_path)
    workspace_paths = resolve_workspace_paths(settings, project_root_path)
    app_id = default_app_id(project_root_path)
    comfy_bind = str(comfy_settings.get("bind_address", "127.0.0.1"))
    comfy_port = resolve_registered_port(
        app_id=app_id,
        service_name="comfyui",
        preferred_port=int(comfy_settings.get("port", 8188)),
        host=comfy_bind,
    )
    comfy_base_url = f"http://{comfy_bind}:{comfy_port}"
    health_endpoint = str(comfy_settings.get("health_endpoint", "/system_stats"))
    comfy_probe = _probe_http(f"{comfy_base_url}{health_endpoint}", timeout=timeout)
    comfy_port_status = describe_service_port(
        comfy_bind,
        comfy_port,
        probe_url=f"{comfy_base_url}{health_endpoint}",
        probe=comfy_probe,
        timeout=timeout,
    )

    assistant_repo_path, assistant_repo_source = resolve_assistant_repo_path(settings, project_root_path)
    assistant_repo_exists = bool(assistant_repo_path) and Path(assistant_repo_path).exists()
    planner_base_url = resolve_planner_base_url(settings, project_root_path)
    planner_health = planner_health_url(settings, project_root_path)
    planner_probe_timeout = max(5.0, timeout)
    planner_probe = _probe_http(planner_health, timeout=planner_probe_timeout)
    planner_host, planner_port = split_base_url_host_port(
        planner_base_url,
        default_port=8000,
    )
    planner_port_status = describe_service_port(
        planner_host,
        planner_port,
        probe_url=planner_health,
        probe=planner_probe,
        timeout=planner_probe_timeout,
    )

    return {
        "manifest_path": str(manifest_file.resolve()),
        "settings_path": str(settings_file.resolve()),
        "settings_exists": settings_file.exists(),
        "project_root": str(project_root_path.resolve()),
        "tool_paths": {key: str(value.resolve()) for key, value in tool_paths.items()},
        "workspace": {
            "base_dir": str(workspace_paths["base_dir"].resolve()),
            "generated_workflows_dir": str(workspace_paths["generated_workflows_dir"].resolve()),
            "generated_workflows_dir_exists": workspace_paths["generated_workflows_dir"].exists(),
        },
        "comfyui": {
            "installed": comfy_root.exists() and comfy_main.exists(),
            "runnable": comfy_root.exists() and comfy_main.exists() and python_executable.exists(),
            "reachable": comfy_probe["reachable"],
            "health_ok": comfy_probe["ok"],
            "repo_source": comfy_settings.get("repo_source") or manifest.get("comfyui_runtime", {}).get("comfyui_repo_source"),
            "repo_path": str(comfy_root.resolve()),
            "main_py": str(comfy_main.resolve()),
            "python_version_constraints": manifest.get("comfyui_runtime", {}).get("python_version_constraints"),
            "python_executable": str(python_executable.resolve()),
            "python_executable_exists": python_executable.exists(),
            "bind_address": comfy_bind,
            "port": comfy_port,
            "base_url": comfy_base_url,
            "health_endpoint": health_endpoint,
            "object_info_endpoint": str(comfy_settings.get("object_info_endpoint", "/object_info")),
            "launch_args": list(comfy_settings.get("launch_args", [])),
            "probe": comfy_probe,
            "port_status": comfy_port_status,
        },
        "planner": {
            "base_url": planner_base_url,
            "health_endpoint": str(planner_settings.get("health_endpoint", "/health")),
            "health_url": planner_health,
            "reachable": planner_probe["reachable"],
            "healthy": planner_probe["ok"],
            "probe": planner_probe,
            "port_status": planner_port_status,
            "optional": bool(planner_settings.get("optional", True)),
            "can_launch_as_sidecar": bool(planner_settings.get("can_launch_as_sidecar", False)),
            "assistant_repo_path": str(assistant_repo_path.resolve()) if assistant_repo_path is not None else None,
            "assistant_repo_path_source": assistant_repo_source,
            "assistant_repo_configured": bool(assistant_repo_path),
            "assistant_repo_exists": assistant_repo_exists,
            "sidecar_launch": planner_settings.get("sidecar_launch", {}),
            "auto_best_ladder_cache": planner_settings.get("auto_best_ladder_cache", {}),
        },
        "optional_models": _collect_optional_models_status(manifest, project_root_path, comfy_root),
        "ports": repo_port_status(app_id=app_id),
    }
