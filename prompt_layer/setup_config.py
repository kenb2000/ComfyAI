"""Settings and path resolution for ComfyUIhybrid setup flows."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "requirements" / "comfyhybrid_requirements.json"
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "settings.json"


def load_requirements_manifest(manifest_path: Path | str | None = None) -> dict[str, Any]:
    path = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _serialize_path(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _default_planner_sidecar_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "cwd": "",
        "windows_command": [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/run_backend_windows.ps1",
        ],
        "linux_command": [
            "bash",
            "scripts/run_backend_linux.sh",
        ],
        "environment": {
            "PLANNER_BASE_URL": "{planner_base_url}",
            "PYTHON_EXECUTABLE": "{python}",
        },
    }


def _default_auto_best_ladder_cache() -> dict[str, Any]:
    return {
        "available": False,
        "saved_at": None,
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


def _backfill_legacy_settings(merged: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    planner = merged.setdefault("planner", {})
    default_planner = defaults.get("planner", {})
    planner.setdefault("health_endpoint", default_planner.get("health_endpoint", "/health"))
    planner["auto_best_ladder_cache"] = deep_merge(
        _default_auto_best_ladder_cache(),
        planner.get("auto_best_ladder_cache", {}),
    )

    launch = planner.setdefault("sidecar_launch", {})
    default_launch = default_planner.get("sidecar_launch", _default_planner_sidecar_config())
    legacy_blank_launcher = (
        not launch.get("windows_command")
        and not launch.get("linux_command")
    )

    if not launch.get("windows_command"):
        launch["windows_command"] = deepcopy(default_launch.get("windows_command", []))
    if not launch.get("linux_command"):
        launch["linux_command"] = deepcopy(default_launch.get("linux_command", []))
    if not launch.get("environment"):
        launch["environment"] = deepcopy(default_launch.get("environment", {}))
    if legacy_blank_launcher or "can_launch_as_sidecar" not in planner:
        planner["can_launch_as_sidecar"] = bool(default_planner.get("can_launch_as_sidecar", True))
    if legacy_blank_launcher or "enabled" not in launch:
        launch["enabled"] = bool(default_launch.get("enabled", True))

    return merged


def default_settings(
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    manifest = load_requirements_manifest(manifest_file)
    comfy_config = manifest.get("comfyui_runtime", {})
    planner_config = manifest.get("planner_service", {})
    venv_policy = comfy_config.get("venv_path_policy", {})
    venv_mode = str(venv_policy.get("mode", "tool_folder")).strip() or "tool_folder"

    tool_base = project_root_path / "tools"
    runtime_dir = tool_base / "runtime"
    if venv_mode == "inside_repo":
        default_venv_dir = resolve_path(
            project_root_path,
            venv_policy.get("inside_repo_relative_path", ".venv"),
        ) or (project_root_path / ".venv")
    else:
        default_venv_dir = resolve_path(
            project_root_path,
            venv_policy.get("tool_folder_relative_path", "tools/comfyhybrid-venv"),
        ) or (tool_base / "comfyhybrid-venv")
    defaults = {
        "schema_version": 1,
        "manifest_path": _serialize_path(project_root_path, manifest_file),
        "tool_paths": {
            "base_dir": _serialize_path(project_root_path, tool_base),
            "venv_dir": _serialize_path(project_root_path, default_venv_dir),
            "downloads_dir": _serialize_path(project_root_path, tool_base / "downloads"),
            "runtime_dir": _serialize_path(project_root_path, runtime_dir),
        },
        "workspace": {
            "base_dir": _serialize_path(project_root_path, tool_base / "workspace"),
            "generated_workflows_dir": _serialize_path(project_root_path, tool_base / "workspace" / "generated-workflows"),
        },
        "comfyui": {
            "repo_source": comfy_config.get("comfyui_repo_source", ""),
            "repo_path": comfy_config.get("local_repo_relative_path", "comfyui"),
            "python_executable": "",
            "bind_address": comfy_config.get("bind_address", "127.0.0.1"),
            "port": int(comfy_config.get("comfyui_port", 8188)),
            "launch_args": ["--enable-manager"],
            "health_endpoint": "/system_stats",
            "object_info_endpoint": "/object_info",
            "log_path": _serialize_path(project_root_path, runtime_dir / "comfyui.log"),
            "pid_path": _serialize_path(project_root_path, runtime_dir / "comfyui.pid"),
        },
        "planner": {
            "base_url": planner_config.get("planner_base_url", "http://127.0.0.1:8000"),
            "health_endpoint": planner_config.get("health_endpoint", "/health"),
            "optional": True,
            "assistant_repo_path": planner_config.get("assistant_repo_path", ""),
            "assistant_repo_path_env_var": planner_config.get("assistant_repo_path_env_var", "COMFYHYBRID_ASSISTANT_REPO"),
            "can_launch_as_sidecar": bool(planner_config.get("can_launch_planner_as_sidecar", True)),
            "auto_best_ladder_cache": _default_auto_best_ladder_cache(),
            "log_path": _serialize_path(project_root_path, runtime_dir / "planner.log"),
            "pid_path": _serialize_path(project_root_path, runtime_dir / "planner.pid"),
            "sidecar_launch": _default_planner_sidecar_config(),
        },
    }
    return defaults


def load_settings(
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH

    defaults = default_settings(project_root=project_root_path, manifest_path=manifest_file)
    if settings_file.exists():
        current = json.loads(settings_file.read_text(encoding="utf-8"))
        return _backfill_legacy_settings(deep_merge(defaults, current), defaults)
    return _backfill_legacy_settings(defaults, defaults)


def save_settings(settings: dict[str, Any], settings_path: Path | str | None = None) -> Path:
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings_file


def resolve_path(project_root: Path | str, raw_path: str | Path | None) -> Path | None:
    if raw_path is None:
        return None
    raw_string = str(raw_path).strip()
    if not raw_string:
        return None
    path = Path(raw_string)
    project_root_path = Path(project_root)
    return path if path.is_absolute() else project_root_path / path


def resolve_tool_paths(settings: dict[str, Any], project_root: Path | str) -> dict[str, Path]:
    project_root_path = Path(project_root)
    tool_paths = settings.get("tool_paths", {})
    return {
        "base_dir": resolve_path(project_root_path, tool_paths.get("base_dir")) or (project_root_path / "tools"),
        "venv_dir": resolve_path(project_root_path, tool_paths.get("venv_dir")) or (project_root_path / "tools" / "comfyhybrid-venv"),
        "downloads_dir": resolve_path(project_root_path, tool_paths.get("downloads_dir")) or (project_root_path / "tools" / "downloads"),
        "runtime_dir": resolve_path(project_root_path, tool_paths.get("runtime_dir")) or (project_root_path / "tools" / "runtime"),
    }


def resolve_workspace_paths(settings: dict[str, Any], project_root: Path | str) -> dict[str, Path]:
    project_root_path = Path(project_root)
    workspace = settings.get("workspace", {})
    tool_base = resolve_tool_paths(settings, project_root_path)["base_dir"]
    default_base = tool_base / "workspace"
    return {
        "base_dir": resolve_path(project_root_path, workspace.get("base_dir")) or default_base,
        "generated_workflows_dir": resolve_path(project_root_path, workspace.get("generated_workflows_dir")) or (default_base / "generated-workflows"),
    }


def resolve_comfy_repo_path(settings: dict[str, Any], project_root: Path | str) -> Path:
    project_root_path = Path(project_root)
    raw_path = settings.get("comfyui", {}).get("repo_path", "comfyui")
    return resolve_path(project_root_path, raw_path) or (project_root_path / "comfyui")


def resolve_python_executable(settings: dict[str, Any], project_root: Path | str) -> Path:
    project_root_path = Path(project_root)
    comfy_settings = settings.get("comfyui", {})
    explicit_python = resolve_path(project_root_path, comfy_settings.get("python_executable"))
    if explicit_python is not None:
        return explicit_python

    venv_dir = resolve_tool_paths(settings, project_root_path)["venv_dir"]
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def resolve_assistant_repo_path(settings: dict[str, Any], project_root: Path | str) -> tuple[Path | None, str]:
    project_root_path = Path(project_root)
    planner_settings = settings.get("planner", {})
    env_var = planner_settings.get("assistant_repo_path_env_var", "")
    env_value = os.environ.get(env_var, "").strip() if env_var else ""
    if env_value:
        return resolve_path(project_root_path, env_value), "env"

    configured = planner_settings.get("assistant_repo_path", "")
    resolved = resolve_path(project_root_path, configured)
    if resolved is not None:
        return resolved, "settings"

    return None, "unset"
