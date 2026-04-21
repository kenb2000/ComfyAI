"""Acquire, launch, and verify flows for ComfyUIhybrid setup."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import socket
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlsplit

from .ports import (
    allocate_port,
    default_app_id,
    default_port_range,
    record_reservation,
    release_reservation,
    resolve_base_url,
    resolve_registered_port,
    write_local_port_state,
)
from .linux_workstation import (
    build_linux_benchmark,
    build_linux_runtime_plan,
    detect_linux_workstation_capabilities,
    scan_ltx_node_state,
)
from .planner import LocalPlannerRuntime
from .planner.config import (
    build_local_planner_policy,
    build_local_planner_status,
    resolve_local_planner_model_path,
    resolve_planner_output_dir,
    write_local_planner_policy,
    write_local_planner_status,
)
from .setup_config import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_SETTINGS_PATH,
    PROJECT_ROOT,
    deep_merge,
    load_requirements_manifest,
    load_settings,
    resolve_assistant_repo_path,
    resolve_comfy_repo_path,
    resolve_path,
    resolve_python_executable,
    resolve_tool_paths,
    save_settings,
)


ProgressEmitter = Callable[[dict[str, Any]], None]
COMFYUI_SERVICE_NAME = "comfyui"
PLANNER_SERVICE_NAME = "planner"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_progress_event(event: str, **payload: Any) -> dict[str, Any]:
    data = {"event": event, "timestamp": _now_iso()}
    data.update(payload)
    return data


def _probe_http(url: str, timeout: float = 2.0) -> dict[str, Any]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            status_code = response.getcode()
            return {
                "reachable": True,
                "ok": 200 <= status_code < 300,
                "status_code": status_code,
                "detail": "ok",
            }
    except error.HTTPError as exc:
        return {
            "reachable": True,
            "ok": False,
            "status_code": exc.code,
            "detail": str(exc),
        }
    except Exception as exc:
        return {
            "reachable": False,
            "ok": False,
            "status_code": None,
            "detail": str(exc),
        }


def _probe_json_http(url: str, timeout: float = 2.0) -> tuple[dict[str, Any], Any | None]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body) if body.strip() else None
        return (
            {
                "reachable": True,
                "ok": 200 <= status_code < 300,
                "status_code": status_code,
                "detail": "ok",
            },
            payload,
        )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        parsed: Any | None = None
        if detail.strip():
            try:
                parsed = json.loads(detail)
            except json.JSONDecodeError:
                parsed = None
        return (
            {
                "reachable": True,
                "ok": False,
                "status_code": exc.code,
                "detail": str(exc),
            },
            parsed,
        )
    except Exception as exc:
        return (
            {
                "reachable": False,
                "ok": False,
                "status_code": None,
                "detail": str(exc),
            },
            None,
        )


def _post_json_http(url: str, payload: dict[str, Any], timeout: float = 5.0) -> tuple[dict[str, Any], Any | None]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            data = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(data) if data.strip() else None
        return (
            {
                "reachable": True,
                "ok": 200 <= status_code < 300,
                "status_code": status_code,
                "detail": "ok",
            },
            parsed,
        )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        parsed: Any | None = None
        if detail.strip():
            try:
                parsed = json.loads(detail)
            except json.JSONDecodeError:
                parsed = None
        return (
            {
                "reachable": True,
                "ok": False,
                "status_code": exc.code,
                "detail": str(exc),
            },
            parsed,
        )
    except Exception as exc:
        return (
            {
                "reachable": False,
                "ok": False,
                "status_code": None,
                "detail": str(exc),
            },
            None,
        )


def _normalize_connect_host(host: str) -> str:
    value = str(host or "").strip().strip("[]")
    if not value or value == "0.0.0.0":
        return "127.0.0.1"
    if value == "::":
        return "::1"
    return value


def split_base_url_host_port(base_url: str, default_port: int | None = None) -> tuple[str, int]:
    parsed = urlsplit(str(base_url).strip())
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    if parsed.port is not None:
        return host, int(parsed.port)
    if default_port is not None:
        return host, int(default_port)
    return host, 443 if parsed.scheme == "https" else 80


def _replace_base_url_port(base_url: str, port: int) -> str:
    parsed = urlsplit(str(base_url).strip())
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    host_value = f"[{host}]" if ":" in host and not host.startswith("[") else host
    path = parsed.path or ""
    return f"{parsed.scheme or 'http'}://{host_value}:{int(port)}{path}"


def _effective_comfy_host_port(settings: dict[str, Any], project_root: Path | str) -> tuple[str, int]:
    comfy_settings = settings.get("comfyui", {})
    host = str(comfy_settings.get("bind_address", "127.0.0.1"))
    preferred_port = int(comfy_settings.get("port", 8188))
    assigned_port = resolve_registered_port(
        app_id=default_app_id(project_root),
        service_name=COMFYUI_SERVICE_NAME,
        preferred_port=preferred_port,
        host=host,
    )
    return host, assigned_port


def resolve_planner_base_url(settings: dict[str, Any], project_root: Path | str) -> str:
    planner_settings = settings.get("planner", {})
    return resolve_base_url(
        base_url=str(planner_settings.get("base_url", "http://127.0.0.1:8000")),
        app_id=default_app_id(project_root),
        service_name=PLANNER_SERVICE_NAME,
        default_port=8000,
    )


def _tcp_port_accepting_connections(host: str, port: int, timeout: float = 1.0) -> bool:
    connect_host = _normalize_connect_host(host)
    connect_timeout = min(max(timeout, 0.05), 0.5)
    try:
        infos = socket.getaddrinfo(connect_host, int(port), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for family, socktype, proto, _, sockaddr in infos:
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(connect_timeout)
            try:
                sock.connect(sockaddr)
            except OSError:
                continue
            return True
    return False


def describe_service_port(
    host: str,
    port: int,
    *,
    probe_url: str | None = None,
    probe: dict[str, Any] | None = None,
    timeout: float = 1.0,
) -> dict[str, Any]:
    resolved_probe = probe if probe is not None else (_probe_http(probe_url, timeout=timeout) if probe_url else None)
    occupied = _tcp_port_accepting_connections(host, port, timeout=timeout)
    expected_service_running = bool(resolved_probe and resolved_probe.get("ok"))
    conflict = occupied and not expected_service_running

    if expected_service_running:
        status = "expected_service_running"
    elif occupied:
        status = "occupied_by_other_service"
    else:
        status = "free"

    return {
        "host": str(host),
        "connect_host": _normalize_connect_host(host),
        "port": int(port),
        "occupied": occupied,
        "expected_service_running": expected_service_running,
        "conflict": conflict,
        "status": status,
        "probe": resolved_probe,
    }


def ensure_launch_target_available(
    service_name: str,
    host: str,
    port: int,
    *,
    probe_url: str | None = None,
    probe: dict[str, Any] | None = None,
    timeout: float = 1.0,
) -> dict[str, Any]:
    port_status = describe_service_port(host, port, probe_url=probe_url, probe=probe, timeout=timeout)
    if port_status["conflict"]:
        probe_detail = ""
        resolved_probe = port_status.get("probe")
        if isinstance(resolved_probe, dict):
            detail = str(resolved_probe.get("detail", "")).strip()
            status_code = resolved_probe.get("status_code")
            if status_code is not None:
                probe_detail = f" Health probe returned {status_code}."
            elif detail:
                probe_detail = f" Health probe failed: {detail}."
        raise RuntimeError(
            f"Configured {service_name} port {host}:{port} is already in use by another service."
            f"{probe_detail} Update settings.json to a free port or stop the conflicting process."
        )
    return port_status


def wait_for_http(
    url: str,
    timeout: float = 30.0,
    interval: float = 1.0,
    require_ok: bool = True,
    probe_timeout: float | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_probe: dict[str, Any] = {"reachable": False, "ok": False, "status_code": None, "detail": "not_started"}
    while time.time() < deadline:
        resolved_probe_timeout = probe_timeout if probe_timeout is not None else min(interval, 2.0)
        last_probe = _probe_http(url, timeout=resolved_probe_timeout)
        if last_probe["reachable"] and (last_probe["ok"] or not require_ok):
            return last_probe
        time.sleep(interval)
    return last_probe


def wait_for_unreachable(url: str, timeout: float = 15.0, interval: float = 0.5) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_probe: dict[str, Any] = {"reachable": True, "ok": True, "status_code": None, "detail": "not_started"}
    while time.time() < deadline:
        last_probe = _probe_http(url, timeout=min(interval, 2.0))
        if not last_probe["reachable"]:
            return last_probe
        time.sleep(interval)
    return last_probe


def _parse_model_source(item: dict[str, Any]) -> tuple[str, str]:
    source = item.get("source", "")
    if isinstance(source, dict):
        return str(source.get("kind", "")).strip(), str(source.get("value", "")).strip()
    return str(source).strip(), str(item.get("source_value", "")).strip()


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_model_file(path: Path, size_min_bytes: int, expected_sha256: str | None) -> tuple[bool, str]:
    if not path.exists() or not path.is_file():
        return False, "missing"
    if path.stat().st_size < size_min_bytes:
        return False, "invalid_size"
    if expected_sha256:
        actual = _sha256_for_file(path).lower()
        if actual != str(expected_sha256).lower():
            return False, "invalid_sha256"
    return True, "present"


def _tail_text(value: str, max_chars: int = 1200) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _read_text_tail(path: Path, max_chars: int = 4000) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    return _tail_text(stripped, max_chars=max_chars)


def _parse_version_tuple(value: str | None) -> tuple[int, ...]:
    parsed: list[int] = []
    for token in str(value or "").replace("-", ".").split("."):
        digits = "".join(character for character in token if character.isdigit())
        if not digits:
            continue
        parsed.append(int(digits))
    return tuple(parsed)


def _version_at_least(actual: tuple[int, ...], expected: tuple[int, ...]) -> bool:
    if not actual:
        return False
    padded = actual + (0,) * max(0, len(expected) - len(actual))
    return padded[: len(expected)] >= expected


def _inspect_installed_torch_versions(python_executable: Path) -> dict[str, Any]:
    script = (
        "import importlib, json\n"
        "result = {}\n"
        "for name in ('torch', 'torchvision', 'torchaudio'):\n"
        "    try:\n"
        "        module = importlib.import_module(name)\n"
        "        result[name] = {'version': getattr(module, '__version__', None), 'ok': True}\n"
        "    except Exception as exc:\n"
        "        result[name] = {'version': None, 'ok': False, 'detail': str(exc)}\n"
        "print(json.dumps(result))\n"
    )
    result = run_command([str(python_executable), "-c", script], timeout=20)
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _torch_versions_match_channel(installed: dict[str, Any], channel: str) -> bool:
    expected_suffix = f"+{channel}"
    for package_name in ("torch", "torchvision", "torchaudio"):
        entry = installed.get(package_name)
        if not isinstance(entry, dict):
            return False
        version = str(entry.get("version") or "").strip()
        if expected_suffix not in version:
            return False
    return True


def _select_linux_nvidia_torch_channel(driver_version: str | None) -> dict[str, Any]:
    parsed_driver = _parse_version_tuple(driver_version)
    if _version_at_least(parsed_driver, (580, 0)):
        channel = "cu130"
        reason = "nvidia_driver_580_or_newer"
    elif _version_at_least(parsed_driver, (550, 0)):
        channel = "cu124"
        reason = "nvidia_driver_550_or_newer"
    else:
        channel = "cu121"
        reason = "conservative_nvidia_driver_fallback"
    return {
        "driver_version": str(driver_version or "").strip() or None,
        "channel": channel,
        "index_url": f"https://download.pytorch.org/whl/{channel}",
        "reason": reason,
    }


def _build_linux_nvidia_torch_install_command(
    settings: dict[str, Any],
    project_root: Path,
    python_executable: Path,
) -> dict[str, Any] | None:
    if platform.system().lower() != "linux":
        return None

    comfy_root = resolve_comfy_repo_path(settings, project_root)
    capabilities = detect_linux_workstation_capabilities(settings, project_root, comfy_root)
    nvidia = capabilities.get("nvidia_gpu", {})
    if not bool(nvidia.get("present")):
        return None

    selection = _select_linux_nvidia_torch_channel(nvidia.get("driver_version"))
    installed = _inspect_installed_torch_versions(python_executable)
    if _torch_versions_match_channel(installed, str(selection["channel"])):
        return None

    return {
        "label": "install_linux_nvidia_torch",
        "command": [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--index-url",
            str(selection["index_url"]),
            "torch",
            "torchvision",
            "torchaudio",
        ],
        "selection": selection,
        "installed_versions": installed,
    }


def _diagnose_comfyui_log(log_tail: str | None) -> str | None:
    normalized = str(log_tail or "").lower()
    if not normalized:
        return None
    if "the nvidia driver on your system is too old" in normalized or (
        "cuda initialization" in normalized and "driver" in normalized
    ):
        return "Detected a PyTorch CUDA wheel that is newer than the installed NVIDIA driver. Reinstall a driver-compatible torch channel or update the NVIDIA driver."
    if "torch not compiled with cuda enabled" in normalized:
        return "Detected a non-CUDA PyTorch build in the ComfyUI venv. Install an NVIDIA CUDA wheel set before launching ComfyUI."
    return None


def run_command(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def planner_health_url(settings: dict[str, Any], project_root: Path | str | None = None) -> str:
    planner_settings = settings.get("planner", {})
    resolved_project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
    base_url = resolve_planner_base_url(settings, resolved_project_root).rstrip("/")
    endpoint = str(planner_settings.get("health_endpoint", "/health")).strip() or "/health"
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    return f"{base_url}{endpoint}"


def _load_pid_metadata(pid_path: Path) -> dict[str, Any] | None:
    if not pid_path.exists():
        return None
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = run_command(["tasklist", "/FI", f"PID eq {pid}"])
        if result.returncode != 0:
            return False
        output = f"{result.stdout}\n{result.stderr}"
        return str(pid) in output and "No tasks are running" not in output

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process(pid: int, timeout: float = 15.0) -> dict[str, Any]:
    if not _is_process_running(pid):
        return {"pid": pid, "stopped": False, "already_exited": True}

    if os.name == "nt":
        result = run_command(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=timeout)
        stopped = result.returncode == 0 or not _is_process_running(pid)
        return {
            "pid": pid,
            "stopped": stopped,
            "already_exited": False,
            "stdout_tail": _tail_text(result.stdout),
            "stderr_tail": _tail_text(result.stderr),
        }

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"pid": pid, "stopped": False, "already_exited": True}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_process_running(pid):
            return {"pid": pid, "stopped": True, "already_exited": False}
        time.sleep(0.25)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return {"pid": pid, "stopped": True, "already_exited": False, "forced": True}

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _is_process_running(pid):
            return {"pid": pid, "stopped": True, "already_exited": False, "forced": True}
        time.sleep(0.25)

    return {"pid": pid, "stopped": False, "already_exited": False, "forced": True}


def _is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    result = run_command(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0


def _has_submodule(project_root: Path, relative_path: str) -> bool:
    gitmodules = project_root / ".gitmodules"
    if not gitmodules.exists():
        return False
    normalized = relative_path.replace("\\", "/")
    return any(line.strip() == f"path = {normalized}" for line in gitmodules.read_text(encoding="utf-8").splitlines())


def build_settings_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key in ("tool_paths", "comfyui", "planner"):
        value = payload.get(key)
        if isinstance(value, dict):
            overrides[key] = value

    if "bind_address" in payload or "comfyui_port" in payload:
        comfy = overrides.setdefault("comfyui", {})
        if "bind_address" in payload:
            comfy["bind_address"] = payload["bind_address"]
        if "comfyui_port" in payload:
            comfy["port"] = payload["comfyui_port"]

    if "planner_base_url" in payload or "assistant_repo_path" in payload:
        planner = overrides.setdefault("planner", {})
        if "planner_base_url" in payload:
            planner["base_url"] = payload["planner_base_url"]
        if "assistant_repo_path" in payload:
            planner["assistant_repo_path"] = payload["assistant_repo_path"]

    if "can_launch_planner_as_sidecar" in payload:
        planner = overrides.setdefault("planner", {})
        planner["can_launch_as_sidecar"] = bool(payload["can_launch_planner_as_sidecar"])
        launch = planner.setdefault("sidecar_launch", {})
        launch.setdefault("enabled", bool(payload["can_launch_planner_as_sidecar"]))

    return overrides


def configure_settings(
    payload: dict[str, Any],
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> tuple[dict[str, Any], Path]:
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    current = load_settings(settings_path=settings_file, project_root=project_root_path, manifest_path=manifest_file)
    merged = deep_merge(current, build_settings_overrides(payload))
    saved = save_settings(merged, settings_path=settings_file)
    return merged, saved


def acquire_comfyui_repo(settings: dict[str, Any], project_root: Path, emit: ProgressEmitter) -> Path:
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    source = str(settings.get("comfyui", {}).get("repo_source", "")).strip()
    main_py = comfy_root / "main.py"
    relative_path = str(comfy_root.relative_to(project_root)).replace("\\", "/") if comfy_root.is_relative_to(project_root) else str(comfy_root)
    is_submodule = _has_submodule(project_root, relative_path)

    emit(make_progress_event("step", step="acquire_comfyui", status="started", repo_path=str(comfy_root.resolve()), repo_source=source))

    if is_submodule and _is_git_repo(project_root):
        result = run_command(["git", "-C", str(project_root), "submodule", "update", "--init", "--recursive", "--", relative_path])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to initialize ComfyUI submodule")
        emit(make_progress_event("step", step="acquire_comfyui", status="completed", action="submodule_initialized"))
        return comfy_root

    if main_py.exists() and _is_git_repo(comfy_root):
        result = run_command(["git", "-C", str(comfy_root), "pull", "--ff-only", "origin", "master"])
        if result.returncode != 0:
            emit(
                make_progress_event(
                    "step",
                    step="acquire_comfyui",
                    status="warning",
                    message="ComfyUI repo exists but update failed; keeping local checkout.",
                    stdout_tail=_tail_text(result.stdout),
                    stderr_tail=_tail_text(result.stderr),
                )
            )
        else:
            emit(make_progress_event("step", step="acquire_comfyui", status="completed", action="updated"))
        return comfy_root

    if comfy_root.exists() and not main_py.exists():
        if comfy_root.is_dir() and not any(comfy_root.iterdir()):
            comfy_root.rmdir()
        else:
            raise RuntimeError(f"Configured ComfyUI path exists but is not a runnable ComfyUI checkout: {comfy_root}")

    if not source:
        raise RuntimeError("No ComfyUI repo source configured in settings.")

    comfy_root.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(["git", "clone", source, str(comfy_root)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to clone ComfyUI repository")
    emit(make_progress_event("step", step="acquire_comfyui", status="completed", action="cloned"))
    return comfy_root


def create_venv_and_install_requirements(settings: dict[str, Any], project_root: Path, emit: ProgressEmitter) -> Path:
    tool_paths = resolve_tool_paths(settings, project_root)
    venv_dir = tool_paths["venv_dir"]
    runtime_dir = tool_paths["runtime_dir"]
    downloads_dir = tool_paths["downloads_dir"]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    emit(make_progress_event("step", step="create_venv", status="started", venv_dir=str(venv_dir.resolve())))
    python_executable = resolve_python_executable(settings, project_root)
    if not python_executable.exists():
        result = run_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=project_root)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to create venv")
        python_executable = resolve_python_executable(settings, project_root)

    install_plan: list[dict[str, Any]] = [
        {
            "label": "upgrade_pip",
            "command": [str(python_executable), "-m", "pip", "install", "--upgrade", "pip"],
        },
        {
            "label": "install_repo_requirements",
            "command": [str(python_executable), "-m", "pip", "install", "-r", str(project_root / "requirements.txt")],
        },
        {
            "label": "install_editable_repo",
            "command": [str(python_executable), "-m", "pip", "install", "-e", str(project_root)],
        },
    ]

    nvidia_torch_install = _build_linux_nvidia_torch_install_command(settings, project_root, python_executable)
    if nvidia_torch_install is not None:
        install_plan.insert(1, nvidia_torch_install)

    comfy_root = resolve_comfy_repo_path(settings, project_root)
    comfy_requirements = comfy_root / "requirements.txt"
    manager_requirements = comfy_root / "manager_requirements.txt"
    if comfy_requirements.exists():
        install_plan.append(
            {
                "label": "install_comfyui_requirements",
                "command": [str(python_executable), "-m", "pip", "install", "-r", str(comfy_requirements)],
            }
        )
    if manager_requirements.exists():
        install_plan.append(
            {
                "label": "install_comfyui_manager_requirements",
                "command": [str(python_executable), "-m", "pip", "install", "-r", str(manager_requirements)],
            }
        )

    for item in install_plan:
        label = str(item["label"])
        command = list(item["command"])
        extra_payload = {key: value for key, value in item.items() if key not in {"label", "command"}}
        emit(make_progress_event("step", step="create_venv", status="running", command=command, label=label, **extra_payload))
        result = run_command(command, cwd=project_root)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{label} failed")

    emit(make_progress_event("step", step="create_venv", status="completed", python_executable=str(python_executable.resolve())))
    return python_executable


def _copy_local_model(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


def _download_model(url: str, target_path: Path, emit: ProgressEmitter, model_name: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".partial")
    bytes_downloaded = 0
    last_emit = 0
    try:
        with request.urlopen(url, timeout=60) as response, temp_path.open("wb") as handle:
            total_bytes = int(response.headers.get("Content-Length", "0")) or None
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                bytes_downloaded += len(chunk)
                if bytes_downloaded - last_emit >= 8 * 1024 * 1024:
                    emit(
                        make_progress_event(
                            "progress",
                            step="acquire_optional_models",
                            model_name=model_name,
                            bytes_downloaded=bytes_downloaded,
                            total_bytes=total_bytes,
                        )
                    )
                    last_emit = bytes_downloaded
        temp_path.replace(target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def acquire_optional_models(settings: dict[str, Any], project_root: Path, emit: ProgressEmitter) -> dict[str, int]:
    manifest = load_requirements_manifest(resolve_path(project_root, settings.get("manifest_path")) or DEFAULT_MANIFEST_PATH)
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    models_root = comfy_root / "models"
    counts = {"present": 0, "copied": 0, "downloaded": 0, "skipped": 0}

    emit(make_progress_event("step", step="acquire_optional_models", status="started", models_root=str(models_root.resolve())))

    for item in manifest.get("optional_comfy_models", []):
        model_name = item["model_name"]
        size_min = int(item.get("expected_size_min_bytes", 0))
        expected_sha256 = item.get("sha256")
        target_path = models_root / Path(item["target_path_relative_to_models"])
        valid, state = _validate_model_file(target_path, size_min, expected_sha256)
        if valid:
            counts["present"] += 1
            emit(make_progress_event("model", step="acquire_optional_models", status="present", model_name=model_name, target_path=str(target_path.resolve())))
            continue

        source_kind, source_value = _parse_model_source(item)
        if source_kind == "local_path" and source_value:
            source_path = resolve_path(project_root, source_value)
            if source_path is None or not source_path.exists():
                counts["skipped"] += 1
                emit(make_progress_event("model", step="acquire_optional_models", status="skipped", reason="source_missing", model_name=model_name))
                continue
            _copy_local_model(source_path, target_path)
            counts["copied"] += 1
            emit(make_progress_event("model", step="acquire_optional_models", status="copied", model_name=model_name, source_path=str(source_path.resolve()), target_path=str(target_path.resolve())))
        elif source_kind == "http_url" and source_value:
            emit(make_progress_event("model", step="acquire_optional_models", status="downloading", model_name=model_name, source_url=source_value))
            _download_model(source_value, target_path, emit, model_name)
            counts["downloaded"] += 1
            emit(make_progress_event("model", step="acquire_optional_models", status="downloaded", model_name=model_name, target_path=str(target_path.resolve())))
        else:
            counts["skipped"] += 1
            emit(make_progress_event("model", step="acquire_optional_models", status="skipped", reason="source_not_configured", model_name=model_name))
            continue

        valid, state = _validate_model_file(target_path, size_min, expected_sha256)
        if not valid:
            raise RuntimeError(f"Optional model validation failed for {model_name}: {state}")

    emit(make_progress_event("step", step="acquire_optional_models", status="completed", counts=counts))
    return counts


def _linux_artifact_paths(settings: dict[str, Any], project_root: Path) -> dict[str, Path]:
    linux_settings = settings.get("linux_workstation", {})
    artifacts = linux_settings.get("artifacts", {})
    runtime_root = resolve_tool_paths(settings, project_root)["runtime_dir"]
    verification_dir = resolve_path(project_root, artifacts.get("verification_dir")) or (runtime_root / "verification" / "linux")
    benchmark_dir = resolve_path(project_root, artifacts.get("benchmark_dir")) or (runtime_root / "benchmarks" / "linux")
    latest_verification = resolve_path(project_root, artifacts.get("latest_verification")) or (verification_dir / "latest.json")
    latest_benchmark = resolve_path(project_root, artifacts.get("latest_benchmark")) or (benchmark_dir / "latest.json")
    return {
        "verification_dir": verification_dir,
        "benchmark_dir": benchmark_dir,
        "latest_verification": latest_verification,
        "latest_benchmark": latest_benchmark,
    }


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _persist_linux_artifact(
    settings: dict[str, Any],
    project_root: Path,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    paths = _linux_artifact_paths(settings, project_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if kind == "benchmark":
        artifact_dir = paths["benchmark_dir"]
        latest_path = paths["latest_benchmark"]
    else:
        artifact_dir = paths["verification_dir"]
        latest_path = paths["latest_verification"]
    artifact_path = artifact_dir / f"{kind}-{timestamp}.json"
    _write_json_artifact(artifact_path, payload)
    _write_json_artifact(latest_path, payload)
    return {
        "artifact_path": str(artifact_path.resolve()),
        "latest_path": str(latest_path.resolve()),
    }


def acquire_linux_workstation_assets(
    settings: dict[str, Any],
    project_root: Path,
    emit: ProgressEmitter,
    *,
    acquire_all_checkpoints: bool = False,
) -> dict[str, Any]:
    linux_settings = settings.get("linux_workstation", {})
    if os.name == "nt" or not linux_settings.get("enabled", True):
        emit(make_progress_event("step", step="acquire_linux_workstation_assets", status="skipped", reason="not_linux"))
        return {"skipped": True, "reason": "not_linux"}

    manifest = load_requirements_manifest(resolve_path(project_root, settings.get("manifest_path")) or DEFAULT_MANIFEST_PATH)
    linux_manifest = manifest.get("linux_workstation", {})
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    models_root = comfy_root / "models"
    node_state = scan_ltx_node_state(settings, project_root, comfy_root)
    node_source_kind, node_source_value = _parse_model_source(dict(linux_manifest.get("ltx_nodes", {}).get("source", {})))

    emit(
        make_progress_event(
            "step",
            step="acquire_linux_workstation_assets",
            status="started",
            active_profile=linux_settings.get("active_profile", "linux_stable_nvidia"),
        )
    )

    if not node_state.get("available") and node_source_kind == "git_url" and node_source_value:
        candidates = node_state.get("candidates", [])
        target = Path(candidates[0]["absolute_path"]) if candidates else (comfy_root / "custom_nodes" / "ComfyUI-LTXVideo")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _is_git_repo(target):
            result = run_command(["git", "-C", str(target), "pull", "--ff-only", "origin", "main"])
            if result.returncode != 0:
                emit(
                    make_progress_event(
                        "step",
                        step="acquire_linux_workstation_assets",
                        status="warning",
                        message="Configured LTXVideo node path exists but could not be updated.",
                        stderr_tail=_tail_text(result.stderr),
                    )
                )
            else:
                emit(make_progress_event("step", step="acquire_linux_workstation_assets", status="running", message="Updated LTXVideo node checkout."))
        elif not target.exists():
            result = run_command(["git", "clone", node_source_value, str(target)], cwd=project_root)
            if result.returncode != 0:
                emit(
                    make_progress_event(
                        "step",
                        step="acquire_linux_workstation_assets",
                        status="warning",
                        message="Failed to clone configured LTXVideo node source.",
                        stderr_tail=_tail_text(result.stderr),
                    )
                )
            else:
                emit(make_progress_event("step", step="acquire_linux_workstation_assets", status="running", message="Cloned configured LTXVideo node source."))
        node_state = scan_ltx_node_state(settings, project_root, comfy_root)

    node_install_status: dict[str, Any] | None = None
    if node_state.get("available"):
        available_node = next(
            (Path(item["absolute_path"]) for item in node_state.get("candidates", []) if item.get("exists")),
            None,
        )
        node_requirements = (available_node / "requirements.txt") if available_node is not None else None
        python_executable = resolve_python_executable(settings, project_root)
        if node_requirements is not None and node_requirements.exists():
            if not python_executable.exists():
                node_install_status = {
                    "status": "skipped",
                    "reason": "python_executable_missing",
                    "requirements_path": str(node_requirements.resolve()),
                }
                emit(
                    make_progress_event(
                        "step",
                        step="acquire_linux_workstation_assets",
                        status="warning",
                        message="Skipping LTXVideo node requirements because the configured Python executable does not exist yet.",
                        requirements_path=str(node_requirements.resolve()),
                    )
                )
            else:
                emit(
                    make_progress_event(
                        "step",
                        step="acquire_linux_workstation_assets",
                        status="running",
                        message="Installing LTXVideo node requirements.",
                        requirements_path=str(node_requirements.resolve()),
                    )
                )
                result = run_command(
                    [str(python_executable), "-m", "pip", "install", "-r", str(node_requirements)],
                    cwd=available_node,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to install LTXVideo node requirements")
                node_install_status = {
                    "status": "installed",
                    "requirements_path": str(node_requirements.resolve()),
                }

    checkpoint_counts = {"present": 0, "copied": 0, "downloaded": 0, "skipped": 0}
    for item in linux_manifest.get("preferred_checkpoints", []):
        model_name = str(item.get("model_name", "linux_workstation_model"))
        if not acquire_all_checkpoints and not bool(item.get("acquire_by_default", True)):
            checkpoint_counts["skipped"] += 1
            emit(
                make_progress_event(
                    "model",
                    step="acquire_linux_workstation_assets",
                    status="skipped",
                    model_name=model_name,
                    reason="deferred_optional_variant",
                )
            )
            continue
        size_min = int(item.get("expected_size_min_bytes", 0))
        expected_sha256 = item.get("sha256")
        target_path = models_root / Path(item["target_path_relative_to_models"])
        valid, _ = _validate_model_file(target_path, size_min, expected_sha256)
        if valid:
            checkpoint_counts["present"] += 1
            continue

        source_kind, source_value = _parse_model_source(item)
        if source_kind == "local_path" and source_value:
            source_path = resolve_path(project_root, source_value)
            if source_path is not None and source_path.exists():
                _copy_local_model(source_path, target_path)
                checkpoint_counts["copied"] += 1
                emit(make_progress_event("model", step="acquire_linux_workstation_assets", status="copied", model_name=model_name))
            else:
                checkpoint_counts["skipped"] += 1
                emit(make_progress_event("model", step="acquire_linux_workstation_assets", status="skipped", model_name=model_name, reason="source_missing"))
        elif source_kind == "http_url" and source_value:
            _download_model(source_value, target_path, emit, model_name)
            checkpoint_counts["downloaded"] += 1
            emit(make_progress_event("model", step="acquire_linux_workstation_assets", status="downloaded", model_name=model_name))
        else:
            checkpoint_counts["skipped"] += 1

    result = {
        "ltx_nodes": node_state,
        "ltx_node_requirements": node_install_status,
        "preferred_checkpoints": checkpoint_counts,
    }
    emit(make_progress_event("step", step="acquire_linux_workstation_assets", status="completed", result=result))
    return result


def _time_probe(url: str, timeout: float = 2.0, json_payload: bool = False) -> tuple[dict[str, Any], Any | None, float]:
    started = time.perf_counter()
    if json_payload:
        probe, payload = _probe_json_http(url, timeout=timeout)
    else:
        probe = _probe_http(url, timeout=timeout)
        payload = None
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    return probe, payload, elapsed_ms


def _collect_linux_verification(
    settings: dict[str, Any],
    project_root: Path,
    object_info_payload: Any | None,
    request_payload: dict[str, Any],
    emit: ProgressEmitter,
) -> dict[str, Any]:
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    capabilities = detect_linux_workstation_capabilities(settings, project_root, comfy_root, object_info_payload=object_info_payload)
    if not capabilities.get("running_on_linux", False):
        return {
            "enabled": False,
            "role": capabilities.get("role"),
            "role_label": capabilities.get("role_label"),
            "active_profile": capabilities.get("active_profile"),
            "capabilities": capabilities,
            "workflows": {},
            "recommended_config": None,
            "artifact_paths": None,
        }

    preview_request = dict(request_payload.get("preview_workflow", {}))
    preview_request.setdefault("profile", "preview")
    preview_request.setdefault("low_workstation_impact", True)
    preview_plan = build_linux_runtime_plan(settings, capabilities, preview_request)

    fallback_request = dict(request_payload.get("fallback_workflow", {}))
    fallback_request.setdefault("profile", fallback_request.get("workflow_profile") or "quality")
    fallback_request.setdefault("low_workstation_impact", True)
    fallback_plan = build_linux_runtime_plan(settings, capabilities, fallback_request)

    for workflow_name, plan in (("preview", preview_plan), ("fallback", fallback_plan)):
        for warning in plan.get("warnings", []):
            emit(make_progress_event("warning", action="setup_verify", workflow=workflow_name, message=warning))

    verification = {
        "enabled": capabilities.get("enabled", False),
        "role": capabilities.get("role"),
        "role_label": capabilities.get("role_label"),
        "active_profile": capabilities.get("active_profile"),
        "capabilities": capabilities,
        "workflows": {
            "preview": preview_plan,
            "fallback": fallback_plan,
        },
        "recommended_config": {
            "workflow_profile": preview_plan.get("effective_profile"),
            "model_variant": preview_plan.get("selected_model_variant", {}).get("label"),
            "async_offload": preview_plan.get("optimizations", {}).get("async_offload"),
            "pinned_memory": preview_plan.get("optimizations", {}).get("pinned_memory"),
            "weight_streaming": preview_plan.get("optimizations", {}).get("weight_streaming"),
            "low_workstation_impact": preview_plan.get("low_workstation_impact"),
        },
    }
    verification["artifact_paths"] = _persist_linux_artifact(settings, project_root, "verification", verification)
    return verification


def benchmark_linux_workstation(
    payload: dict[str, Any] | None = None,
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    request_payload = payload or {}
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    settings = load_settings(settings_path=settings_file, project_root=project_root_path, manifest_path=manifest_file)

    comfy_settings = settings.get("comfyui", {})
    comfy_host, comfy_port = _effective_comfy_host_port(settings, project_root_path)
    comfy_base = f"http://{comfy_host}:{comfy_port}"
    health_url = f"{comfy_base}{comfy_settings.get('health_endpoint', '/system_stats')}"
    object_info_url = f"{comfy_base}{comfy_settings.get('object_info_endpoint', '/object_info')}"
    launch_if_needed = bool(request_payload.get("launch_comfyui_if_needed", True))
    start_timeout = float(request_payload.get("start_timeout_seconds", 45))

    health_probe, _, health_ms = _time_probe(health_url, timeout=2.0, json_payload=False)
    launched = None
    if not health_probe.get("ok") and launch_if_needed:
        try:
            launched = launch_comfyui_sidecar(settings, project_root_path)
            health_probe = wait_for_http(health_url, timeout=start_timeout, require_ok=True)
            health_ms = round(start_timeout * 1000.0, 2)
        except Exception as exc:
            launched = {"error": str(exc)}

    object_info_probe, object_info_payload, object_info_ms = _time_probe(object_info_url, timeout=4.0, json_payload=True)
    comfy_root = resolve_comfy_repo_path(settings, project_root_path)
    capabilities = detect_linux_workstation_capabilities(settings, project_root_path, comfy_root, object_info_payload=object_info_payload)
    benchmark = build_linux_benchmark(
        settings,
        capabilities,
        measurements={
            "health": {"probe": health_probe, "elapsed_ms": health_ms},
            "object_info": {"probe": object_info_probe, "elapsed_ms": object_info_ms},
        },
    )
    benchmark["settings_path"] = str(settings_file.resolve())
    benchmark["comfyui"] = {
        "base_url": comfy_base,
        "health_url": health_url,
        "object_info_url": object_info_url,
        "launch": launched,
    }
    benchmark["artifact_paths"] = _persist_linux_artifact(settings, project_root_path, "benchmark", benchmark)
    return benchmark


def _write_pid_metadata(pid_path: Path, payload: dict[str, Any]) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _expand_tokens(value: str, mapping: dict[str, str]) -> str:
    expanded = value
    for key, replacement in mapping.items():
        expanded = expanded.replace("{" + key + "}", replacement)
    return expanded


def _validate_launch_command(command: list[str], cwd: Path) -> None:
    if not command:
        raise RuntimeError("Launch command is empty.")

    executable = str(command[0]).strip()
    if not executable:
        raise RuntimeError("Launch command executable is empty.")

    shell_wrappers = {"bash", "sh", "powershell.exe", "pwsh", "pwsh.exe"}
    candidate_path: Path | None = None
    if executable in shell_wrappers and len(command) >= 2:
        candidate_path = Path(command[1])
    else:
        executable_path = Path(executable)
        if executable_path.suffix.lower() in {".sh", ".ps1", ".bat", ".cmd"} or "/" in executable or "\\" in executable:
            candidate_path = executable_path

    if candidate_path is None or str(candidate_path) in {"-c", "/c"}:
        return

    resolved = candidate_path if candidate_path.is_absolute() else cwd / candidate_path
    if not resolved.exists():
        raise RuntimeError(f"Planner launch command references a missing file: {resolved}")


def _spawn_detached(command: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_handle:
        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": env,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            executable = str(command[0]).lower() if command else ""
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if "powershell" not in executable and not executable.endswith(".ps1"):
                creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
                creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        pid = int(process.pid)
        # Detached sidecars are tracked through pid files instead of a live Popen object.
        process._child_created = False  # type: ignore[attr-defined]
    return pid


def build_comfyui_command(settings: dict[str, Any], project_root: Path, assigned_port: int | None = None) -> list[str]:
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    main_py = comfy_root / "main.py"
    python_executable = resolve_python_executable(settings, project_root)
    comfy_settings = settings.get("comfyui", {})
    command = [str(python_executable), str(main_py)]
    launch_args = [str(arg) for arg in comfy_settings.get("launch_args", [])]
    command.extend(launch_args)

    if "--listen" not in launch_args:
        command.extend(["--listen", str(comfy_settings.get("bind_address", "127.0.0.1"))])
    if "--port" not in launch_args:
        command.extend(["--port", str(assigned_port if assigned_port is not None else comfy_settings.get("port", 8188))])
    return command


def launch_comfyui_sidecar(settings: dict[str, Any], project_root: Path, emit: ProgressEmitter | None = None) -> dict[str, Any]:
    comfy_settings = settings.get("comfyui", {})
    comfy_root = resolve_comfy_repo_path(settings, project_root)
    host = str(comfy_settings.get("bind_address", "127.0.0.1"))
    requested_port = int(comfy_settings.get("port", 8188))
    app_id = default_app_id(project_root)
    allocation = allocate_port(
        app_id=app_id,
        service_name=COMFYUI_SERVICE_NAME,
        preferred_port=requested_port,
        host=host,
        allowed_range=default_port_range(requested_port),
        notes=str(project_root.resolve()),
    )
    port = allocation.assigned_port
    health_endpoint = str(comfy_settings.get("health_endpoint", "/system_stats")).strip() or "/system_stats"
    if not health_endpoint.startswith("/"):
        health_endpoint = f"/{health_endpoint}"
    health_url = f"http://{host}:{port}{health_endpoint}"
    port_status = ensure_launch_target_available("ComfyUI", host, port, probe_url=health_url)
    command = build_comfyui_command(settings, project_root, assigned_port=port)
    runtime_paths = resolve_tool_paths(settings, project_root)
    log_path = resolve_path(project_root, comfy_settings.get("log_path")) or (runtime_paths["runtime_dir"] / "comfyui.log")
    pid_path = resolve_path(project_root, comfy_settings.get("pid_path")) or (runtime_paths["runtime_dir"] / "comfyui.pid")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    linux_settings = settings.get("linux_workstation", {})
    env.setdefault("COMFYAI_MACHINE_ROLE", str(linux_settings.get("role", "stable_workstation_development_node")))
    env.setdefault("COMFYAI_MACHINE_PROFILE", str(linux_settings.get("active_profile", "linux_stable_nvidia")))
    env.setdefault("COMFYAI_LOW_WORKSTATION_IMPACT", "1")
    env.setdefault("COMFYAI_ASYNC_OFFLOAD", "1")
    env.setdefault("COMFYAI_PINNED_MEMORY", "1")
    env.setdefault("COMFYAI_WEIGHT_STREAMING", "auto")
    env.setdefault("COMFYAI_NVFP4_SUPPORTED", "0")

    pid = _spawn_detached(command, comfy_root, env, log_path)
    record_reservation(
        app_id=app_id,
        service_name=COMFYUI_SERVICE_NAME,
        protocol="tcp",
        host=host,
        requested_port=requested_port,
        assigned_port=port,
        pid=pid,
        notes=str(project_root.resolve()),
    )
    write_local_port_state(app_id=app_id, project_root=project_root)
    metadata = {
        "pid": pid,
        "command": command,
        "cwd": str(comfy_root.resolve()),
        "log_path": str(log_path.resolve()),
        "started_at": _now_iso(),
        "port_status": port_status,
        "port_allocation": allocation.as_dict(),
        "bound_host": host,
        "bound_port": port,
    }
    _write_pid_metadata(pid_path, metadata)
    if emit is not None:
        emit(make_progress_event("launch", step="verify", target="comfyui", status="started", **metadata))
    return metadata


def build_planner_launch_spec(
    settings: dict[str, Any],
    project_root: Path,
    *,
    port_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planner_settings = settings.get("planner", {})
    launch_settings = planner_settings.get("sidecar_launch", {})
    assistant_repo_path, assistant_repo_source = resolve_assistant_repo_path(settings, project_root)
    if assistant_repo_path is None or not assistant_repo_path.exists():
        raise RuntimeError("Planner assistant repo path is not configured or does not exist.")
    if not launch_settings.get("enabled", False):
        raise RuntimeError("Planner sidecar launch is not enabled in settings.")
    if not planner_settings.get("can_launch_as_sidecar", False):
        raise RuntimeError("Planner sidecar launch is disabled in settings.")

    command_key = "windows_command" if os.name == "nt" else "linux_command"
    command_template = launch_settings.get(command_key, [])
    if not command_template:
        raise RuntimeError(f"No planner sidecar command configured for {command_key}.")

    configured_planner_url = str(planner_settings.get("base_url", "http://127.0.0.1:8000"))
    planner_host, requested_port = split_base_url_host_port(configured_planner_url, default_port=8000)
    app_id = default_app_id(project_root)
    allocation = allocate_port(
        app_id=app_id,
        service_name=PLANNER_SERVICE_NAME,
        preferred_port=requested_port,
        host=planner_host,
        allowed_range=default_port_range(requested_port),
        notes=str(project_root.resolve()),
    )
    planner_port = allocation.assigned_port
    planner_url = _replace_base_url_port(configured_planner_url, planner_port)
    resolved_settings = {**settings, "planner": {**planner_settings, "base_url": planner_url}}
    resolved_port_status = port_status or ensure_launch_target_available(
        "planner service",
        planner_host,
        planner_port,
        probe_url=planner_health_url(resolved_settings, project_root),
    )
    if resolved_port_status.get("conflict"):
        ensure_launch_target_available(
            "planner service",
            planner_host,
            planner_port,
            probe_url=planner_health_url(resolved_settings, project_root),
            probe=resolved_port_status.get("probe"),
        )
    mapping = {
        "assistant_repo": str(assistant_repo_path.resolve()),
        "project_root": str(project_root.resolve()),
        "planner_base_url": planner_url,
        "python": sys.executable,
    }
    command = [_expand_tokens(str(part), mapping) for part in command_template]
    cwd_raw = str(launch_settings.get("cwd", "")).strip()
    cwd = resolve_path(project_root, _expand_tokens(cwd_raw, mapping)) if cwd_raw else assistant_repo_path
    _validate_launch_command(command, cwd)
    env = os.environ.copy()
    for key, value in dict(launch_settings.get("environment", {})).items():
        env[key] = _expand_tokens(str(value), mapping)

    runtime_paths = resolve_tool_paths(settings, project_root)
    log_path = resolve_path(project_root, planner_settings.get("log_path")) or (runtime_paths["runtime_dir"] / "planner.log")
    pid_path = resolve_path(project_root, planner_settings.get("pid_path")) or (runtime_paths["runtime_dir"] / "planner.pid")
    return {
        "command": command,
        "cwd": cwd,
        "env": env,
        "log_path": log_path,
        "pid_path": pid_path,
        "assistant_repo_path": assistant_repo_path,
        "assistant_repo_path_source": assistant_repo_source,
        "command_key": command_key,
        "planner_url": planner_url,
        "health_url": planner_health_url(resolved_settings, project_root),
        "port_status": resolved_port_status,
        "port_allocation": allocation.as_dict(),
        "requested_port": requested_port,
        "app_id": app_id,
        "host": planner_host,
    }


def launch_planner_sidecar(settings: dict[str, Any], project_root: Path, emit: ProgressEmitter | None = None) -> dict[str, Any]:
    spec = build_planner_launch_spec(settings, project_root)
    pid = _spawn_detached(spec["command"], spec["cwd"], spec["env"], spec["log_path"])
    record_reservation(
        app_id=spec["app_id"],
        service_name=PLANNER_SERVICE_NAME,
        protocol="tcp",
        host=spec["host"],
        requested_port=spec["requested_port"],
        assigned_port=int(spec["port_allocation"]["assigned_port"]),
        pid=pid,
        notes=str(project_root.resolve()),
    )
    write_local_port_state(app_id=spec["app_id"], project_root=project_root)
    metadata = {
        "pid": pid,
        "command": spec["command"],
        "cwd": str(spec["cwd"].resolve()),
        "log_path": str(spec["log_path"].resolve()),
        "pid_path": str(spec["pid_path"].resolve()),
        "assistant_repo_path": str(spec["assistant_repo_path"].resolve()),
        "assistant_repo_path_source": spec["assistant_repo_path_source"],
        "health_url": spec["health_url"],
        "started_at": _now_iso(),
        "planner_url": spec["planner_url"],
        "port_allocation": spec["port_allocation"],
    }
    _write_pid_metadata(spec["pid_path"], metadata)
    if emit is not None:
        emit(make_progress_event("launch", step="verify", target="planner", status="started", **metadata))
    return metadata


def planner_service_status(
    settings: dict[str, Any],
    project_root: Path,
    settings_path: Path | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    planner_settings = settings.get("planner", {})
    assistant_repo_path, assistant_repo_source = resolve_assistant_repo_path(settings, project_root)
    assistant_repo_exists = bool(assistant_repo_path) and assistant_repo_path.exists()
    try:
        launch_spec = build_planner_launch_spec(settings, project_root)
        launch_error = None
    except Exception as exc:
        launch_spec = None
        launch_error = str(exc)

    base_url = launch_spec["planner_url"] if launch_spec is not None else resolve_planner_base_url(settings, project_root)
    health_url = launch_spec["health_url"] if launch_spec is not None else planner_health_url(settings, project_root)
    planner_probe_timeout = max(5.0, timeout)
    if launch_spec is not None:
        probe = _probe_http(health_url, timeout=planner_probe_timeout)
        port_status = describe_service_port(
            launch_spec["host"],
            int(launch_spec["port_allocation"]["assigned_port"]),
            probe_url=health_url,
            probe=probe,
            timeout=planner_probe_timeout,
        )
        planner_host, planner_port = split_base_url_host_port(base_url, default_port=8000)
    else:
        probe = _probe_http(health_url, timeout=planner_probe_timeout)
        planner_host, planner_port = split_base_url_host_port(base_url, default_port=8000)
        port_status = describe_service_port(
            planner_host,
            planner_port,
            probe_url=health_url,
            probe=probe,
            timeout=planner_probe_timeout,
        )

    runtime_paths = resolve_tool_paths(settings, project_root)
    pid_path = resolve_path(project_root, planner_settings.get("pid_path")) or (runtime_paths["runtime_dir"] / "planner.pid")
    log_path = resolve_path(project_root, planner_settings.get("log_path")) or (runtime_paths["runtime_dir"] / "planner.log")
    pid_info = _load_pid_metadata(pid_path)
    pid = int(pid_info.get("pid", 0)) if isinstance(pid_info, dict) and pid_info.get("pid") else 0
    pid_running = _is_process_running(pid) if pid else False

    return {
        "settings_path": str(settings_path.resolve()) if settings_path is not None else None,
        "base_url": base_url,
        "health_endpoint": str(planner_settings.get("health_endpoint", "/health")),
        "health_url": health_url,
        "healthy": bool(probe.get("ok", False)),
        "reachable": bool(probe.get("reachable", False)),
        "probe": probe,
        "port_status": port_status,
        "assistant_repo_path": str(assistant_repo_path.resolve()) if assistant_repo_path is not None else None,
        "assistant_repo_path_source": assistant_repo_source,
        "assistant_repo_exists": assistant_repo_exists,
        "can_launch_as_sidecar": bool(planner_settings.get("can_launch_as_sidecar", False)),
        "launch_enabled": bool(planner_settings.get("sidecar_launch", {}).get("enabled", False)),
        "command_configured": launch_spec is not None,
        "command_preview": launch_spec["command"] if launch_spec is not None else None,
        "command_key": launch_spec["command_key"] if launch_spec is not None else ("windows_command" if os.name == "nt" else "linux_command"),
        "cwd": str(launch_spec["cwd"].resolve()) if launch_spec is not None else (str(assistant_repo_path.resolve()) if assistant_repo_path is not None else None),
        "log_path": str(log_path.resolve()),
        "pid_path": str(pid_path.resolve()),
        "pid_info": pid_info,
        "pid_running": pid_running,
        "can_start": launch_spec is not None and assistant_repo_exists and not port_status["conflict"],
        "can_stop": pid_running,
        "launch_error": launch_error,
    }


def stop_planner_sidecar(settings: dict[str, Any], project_root: Path) -> dict[str, Any]:
    planner_settings = settings.get("planner", {})
    runtime_paths = resolve_tool_paths(settings, project_root)
    pid_path = resolve_path(project_root, planner_settings.get("pid_path")) or (runtime_paths["runtime_dir"] / "planner.pid")
    pid_info = _load_pid_metadata(pid_path)
    if pid_info is None or not pid_info.get("pid"):
        if pid_path.exists():
            pid_path.unlink()
        return {"stopped": False, "already_exited": True, "pid": None, "pid_path": str(pid_path.resolve())}

    pid = int(pid_info["pid"])
    result = _stop_process(pid)
    result["pid_path"] = str(pid_path.resolve())
    if result.get("stopped") or result.get("already_exited"):
        release_reservation(
            app_id=default_app_id(project_root),
            service_name=PLANNER_SERVICE_NAME,
            host=split_base_url_host_port(resolve_planner_base_url(settings, project_root), default_port=8000)[0],
        )
        write_local_port_state(app_id=default_app_id(project_root), project_root=project_root)
        if pid_path.exists():
            pid_path.unlink()
    return result


def acquire_local_planner_runtime(
    settings: dict[str, Any],
    settings_path: Path,
    project_root: Path,
    emit: ProgressEmitter,
    *,
    allow_download: bool = False,
) -> dict[str, Any]:
    emit(make_progress_event("step", step="acquire_local_planner", status="started"))
    planner_settings = settings.setdefault("planner", {})
    output_dir = resolve_planner_output_dir(settings, project_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_path, model_path_source, inspection = resolve_local_planner_model_path(settings, project_root)
    if not inspection["ok"] and allow_download and model_path is not None:
        python_executable = resolve_python_executable(settings, project_root)
        model_id = str(planner_settings.get("default_model_id", ""))
        download_script = (
            "from huggingface_hub import snapshot_download\n"
            f"snapshot_download(repo_id={model_id!r}, local_dir={str(model_path)!r}, local_dir_use_symlinks=False)\n"
        )
        emit(
            make_progress_event(
                "step",
                step="acquire_local_planner",
                status="running",
                action="download_model",
                model_id=model_id,
                target_path=str(model_path),
            )
        )
        result = run_command([str(python_executable if python_executable.exists() else Path(sys.executable)), "-c", download_script], cwd=project_root)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to download local planner model")
        model_path, model_path_source, inspection = resolve_local_planner_model_path(settings, project_root)

    if model_path is not None:
        planner_settings["model_path"] = str(model_path.resolve()) if inspection["exists"] else str(model_path)

    save_settings(settings, settings_path=settings_path)
    policy_path = write_local_planner_policy(settings, project_root)
    status_payload = build_local_planner_status(settings, project_root, object_info_ok=False)
    status_path = write_local_planner_status(settings, project_root, status_payload)
    if not inspection["ok"]:
        emit(
            make_progress_event(
                "step",
                step="acquire_local_planner",
                status="warning",
                message="Falcon planner model is not registered locally yet.",
                model_path=str(model_path) if model_path is not None else None,
                model_path_source=model_path_source,
                inspection=inspection,
                policy_path=str(policy_path.resolve()),
                status_path=str(status_path.resolve()),
            )
        )
        return settings

    emit(
        make_progress_event(
            "step",
            step="acquire_local_planner",
            status="completed",
            model_id=str(planner_settings.get("default_model_id")),
            model_path=str(model_path.resolve()),
            model_path_source=model_path_source,
            planner_output_dir=str(output_dir.resolve()),
            policy_path=str(policy_path.resolve()),
            status_path=str(status_path.resolve()),
        )
    )
    return settings


def verify_local_planner_runtime(
    settings: dict[str, Any],
    settings_path: Path,
    project_root: Path,
    *,
    object_info_payload: Any,
    comfy_base_url: str | None = None,
    emit: ProgressEmitter | None = None,
) -> dict[str, Any]:
    planner_runtime = LocalPlannerRuntime(settings, project_root)
    result = planner_runtime.verify(object_info_payload, comfy_base_url=comfy_base_url)

    planner_patch = {
        "last_verify_at": result["last_verify_at"],
        "last_verify_ok": bool(result["ok"]),
        "last_verify_summary": str(result.get("summary", "")),
    }
    settings["planner"] = deep_merge(settings.get("planner", {}), planner_patch)
    save_settings(settings, settings_path=settings_path)
    write_local_planner_policy(settings, project_root)
    status_payload = build_local_planner_status(settings, project_root, object_info_ok=bool(object_info_payload))
    status_payload["verify"] = result
    write_local_planner_status(settings, project_root, status_payload)
    if emit is not None:
        emit(make_progress_event("step", step="verify_local_planner", status="completed", ok=bool(result["ok"])))
    return {
        **status_payload,
        "verify": result,
    }


def run_setup_acquire(
    payload: dict[str, Any] | None = None,
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    emit: ProgressEmitter | None = None,
) -> dict[str, Any]:
    request_payload = payload or {}
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    emitter = emit or (lambda event: None)

    steps = {
        "configure_ports": bool(request_payload.get("configure_ports", True)),
        "acquire_comfyui": bool(request_payload.get("acquire_comfyui", True)),
        "create_venv": bool(request_payload.get("create_venv", True)),
        "acquire_local_planner": bool(request_payload.get("acquire_local_planner", True)),
        "acquire_optional_models": bool(request_payload.get("acquire_optional_models", False)),
        "acquire_linux_workstation_assets": bool(request_payload.get("acquire_linux_workstation_assets", os.name != "nt")),
    }
    acquire_all_linux_checkpoints = bool(request_payload.get("acquire_all_linux_checkpoints", False))
    download_local_planner_if_missing = bool(request_payload.get("download_local_planner_if_missing", False))

    emitter(make_progress_event("start", action="setup_acquire", settings_path=str(settings_file.resolve()), steps=steps))
    settings, saved_settings_path = configure_settings(
        request_payload,
        settings_path=settings_file,
        project_root=project_root_path,
        manifest_path=manifest_file,
    )
    emitter(make_progress_event("step", step="configure_ports", status="completed", settings_path=str(saved_settings_path.resolve())))

    if steps["acquire_comfyui"]:
        acquire_comfyui_repo(settings, project_root_path, emitter)
    else:
        emitter(make_progress_event("step", step="acquire_comfyui", status="skipped"))

    if steps["create_venv"]:
        create_venv_and_install_requirements(settings, project_root_path, emitter)
    else:
        emitter(make_progress_event("step", step="create_venv", status="skipped"))

    if steps["acquire_local_planner"]:
        settings = acquire_local_planner_runtime(
            settings,
            settings_file,
            project_root_path,
            emitter,
            allow_download=download_local_planner_if_missing,
        )
    else:
        emitter(make_progress_event("step", step="acquire_local_planner", status="skipped"))

    if steps["acquire_optional_models"]:
        acquire_optional_models(settings, project_root_path, emitter)
    else:
        emitter(make_progress_event("step", step="acquire_optional_models", status="skipped"))

    if steps["acquire_linux_workstation_assets"]:
        acquire_linux_workstation_assets(
            settings,
            project_root_path,
            emitter,
            acquire_all_checkpoints=acquire_all_linux_checkpoints,
        )
    else:
        emitter(make_progress_event("step", step="acquire_linux_workstation_assets", status="skipped"))

    emitter(make_progress_event("complete", action="setup_acquire", ok=True))
    return settings


def verify_setup(
    payload: dict[str, Any] | None = None,
    settings_path: Path | str | None = None,
    project_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    emit: ProgressEmitter | None = None,
) -> dict[str, Any]:
    request_payload = payload or {}
    project_root_path = Path(project_root) if project_root is not None else PROJECT_ROOT
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    settings_file = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    settings = load_settings(settings_path=settings_file, project_root=project_root_path, manifest_path=manifest_file)
    emitter = emit or (lambda event: None)

    comfy_settings = settings.get("comfyui", {})
    planner_settings = settings.get("planner", {})
    comfy_host, comfy_port = _effective_comfy_host_port(settings, project_root_path)
    comfy_base = f"http://{comfy_host}:{comfy_port}"
    health_url = f"{comfy_base}{comfy_settings.get('health_endpoint', '/system_stats')}"
    object_info_url = f"{comfy_base}{comfy_settings.get('object_info_endpoint', '/object_info')}"
    planner_url = resolve_planner_base_url(settings, project_root_path)
    planner_health = planner_health_url(settings, project_root_path)
    start_timeout = float(request_payload.get("start_timeout_seconds", 45))
    launch_comfy_if_needed = bool(request_payload.get("launch_comfyui_if_needed", True))
    launch_planner_if_needed = bool(request_payload.get("launch_planner_if_needed", True))

    health_before = _probe_http(health_url, timeout=2.0)
    comfy_launch = None
    if not health_before["ok"] and launch_comfy_if_needed:
        try:
            comfy_launch = launch_comfyui_sidecar(settings, project_root_path, emit=emitter)
        except Exception as exc:
            comfy_launch = {"error": str(exc)}
    if health_before["ok"]:
        health_after = health_before
    elif comfy_launch is not None and "error" not in comfy_launch:
        health_after = wait_for_http(health_url, timeout=start_timeout, require_ok=True)
    else:
        health_after = health_before
    if health_after["ok"]:
        object_info, object_info_payload = _probe_json_http(object_info_url, timeout=2.0)
        if not object_info["ok"]:
            object_info = wait_for_http(object_info_url, timeout=min(15.0, start_timeout), require_ok=True)
            object_info, object_info_payload = _probe_json_http(object_info_url, timeout=2.0)
    else:
        object_info = {"reachable": False, "ok": False, "status_code": None, "detail": "health_check_failed"}
        object_info_payload = None

    comfy_port_status = describe_service_port(
        comfy_host,
        comfy_port,
        probe_url=health_url,
        probe=health_after,
        timeout=2.0,
    )
    required_ok = health_after["ok"] and object_info["ok"]
    planner_probe_timeout = max(5.0, min(start_timeout, 10.0))
    assistant_service = planner_service_status(
        settings,
        project_root_path,
        settings_path=settings_file,
        timeout=planner_probe_timeout,
    )
    planner_enabled = bool(planner_settings.get("enabled", True))
    assistant_repo_path, assistant_repo_source = resolve_assistant_repo_path(settings, project_root_path)
    if planner_enabled:
        try:
            planner_verification = verify_local_planner_runtime(
                settings,
                settings_file,
                project_root_path,
                object_info_payload=object_info_payload,
                comfy_base_url=comfy_base if health_after["ok"] else None,
                emit=emitter,
            )
            planner_error = None
        except Exception as exc:
            verify_timestamp = _now_iso()
            planner_patch = {
                "last_verify_at": verify_timestamp,
                "last_verify_ok": False,
                "last_verify_summary": str(exc),
            }
            settings["planner"] = deep_merge(settings.get("planner", {}), planner_patch)
            save_settings(settings, settings_path=settings_file)
            planner_verification = build_local_planner_status(
                settings,
                project_root_path,
                object_info_ok=bool(object_info["ok"]),
            )
            planner_verification["verify"] = {
                "ok": False,
                "last_verify_at": verify_timestamp,
                "summary": str(exc),
            }
            write_local_planner_policy(settings, project_root_path)
            write_local_planner_status(settings, project_root_path, planner_verification)
            planner_error = str(exc)
    else:
        planner_verification = build_local_planner_status(
            settings,
            project_root_path,
            object_info_ok=bool(object_info["ok"]),
        )
        planner_error = None
    planner_ok = bool(planner_verification.get("ready")) if planner_enabled else True
    comfy_runtime_paths = resolve_tool_paths(settings, project_root_path)
    comfy_log_path = resolve_path(project_root_path, comfy_settings.get("log_path")) or (comfy_runtime_paths["runtime_dir"] / "comfyui.log")
    comfy_log_tail = _read_text_tail(comfy_log_path) if not health_after["ok"] else None
    comfy_diagnostics = None
    if comfy_launch is not None and ("error" in comfy_launch or not health_after["ok"]):
        comfy_diagnostics = {
            "log_path": str(comfy_log_path.resolve()),
            "log_tail": comfy_log_tail,
            "hint": _diagnose_comfyui_log(comfy_log_tail),
        }
    linux_verification = _collect_linux_verification(
        settings,
        project_root_path,
        object_info_payload,
        request_payload,
        emitter,
    )

    result = {
        "settings_path": str(settings_file.resolve()),
        "comfyui": {
            "base_url": comfy_base,
            "health_url": health_url,
            "object_info_url": object_info_url,
            "health_before": health_before,
            "health_after": health_after,
            "object_info": object_info,
            "port_status": comfy_port_status,
            "launched_by_verify": comfy_launch is not None,
            "launch": comfy_launch,
            "diagnostics": comfy_diagnostics,
        },
        "planner": {
            **planner_verification,
            "enabled": planner_enabled,
            "assistant_service": assistant_service,
            "assistant_repo_path": str(assistant_repo_path.resolve()) if assistant_repo_path is not None else None,
            "assistant_repo_path_source": assistant_repo_source,
            "error": planner_error,
        },
        "linux_workstation": linux_verification,
        "all_required_ok": required_ok and planner_ok,
    }
    emitter(make_progress_event("complete", action="setup_verify", ok=result["all_required_ok"]))
    return result
