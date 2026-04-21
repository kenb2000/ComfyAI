r"""Launch the local ComfyUI server with the repo venv and Manager enabled.

Usage (PowerShell):
    python .\scripts\launch_comfyui.py

Notes:
- Prefers ./.venv/Scripts/python.exe when available.
- Enables ComfyUI-Manager by default.
- Uses GPU by default; set HYBRID_COMFYUI_USE_CPU=1 to force CPU mode.
- Forwards any extra CLI args to ComfyUI.
- Use Ctrl+C in the terminal to stop the server.
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_layer.ports import allocate_port, default_app_id, default_port_range, record_reservation, write_local_port_state
from prompt_layer.setup_config import (
    load_settings,
    resolve_comfy_repo_path,
    resolve_python_executable,
    resolve_tool_paths,
)


def resolve_frontend_root(python_exe: Path) -> Path | None:
    venv_root = python_exe.parent.parent
    candidates = [
        venv_root / "Lib" / "site-packages" / "comfyui_frontend_package" / "static",
        venv_root / "Lib" / "site-packages" / "ComfyUI_frontend_package" / "static",
        venv_root / "lib" / "site-packages" / "comfyui_frontend_package" / "static",
        venv_root / "lib" / "site-packages" / "ComfyUI_frontend_package" / "static",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def main() -> int:
    root = ROOT
    settings = load_settings(project_root=root)
    comfy_dir = resolve_comfy_repo_path(settings, root)
    main_py = comfy_dir / "main.py"
    python_exe = resolve_python_executable(settings, root)
    tool_paths = resolve_tool_paths(settings, root)

    if not comfy_dir.exists() or not main_py.exists():
        print(f"ComfyUI repository not found at {comfy_dir} or missing main.py.")
        print("Run the setup acquire flow or update settings.json to point at a valid checkout.")
        return 2

    env = os.environ.copy()
    env.setdefault("MASTER_PORTS_PATH", str((root / "tools" / "runtime" / "MasterPorts.json").resolve()))
    linux_settings = settings.get("linux_workstation", {})
    env.setdefault("COMFYAI_MACHINE_ROLE", str(linux_settings.get("role", "stable_workstation_development_node")))
    env.setdefault("COMFYAI_MACHINE_PROFILE", str(linux_settings.get("active_profile", "linux_stable_nvidia")))
    env.setdefault("COMFYAI_LOW_WORKSTATION_IMPACT", "1")
    env.setdefault("COMFYAI_ASYNC_OFFLOAD", "1")
    env.setdefault("COMFYAI_PINNED_MEMORY", "1")
    env.setdefault("COMFYAI_WEIGHT_STREAMING", "auto")
    env.setdefault("COMFYAI_NVFP4_SUPPORTED", "0")
    forwarded_args = sys.argv[1:]
    args = [str(python_exe), str(main_py)]

    default_launch_args = [str(arg) for arg in settings.get("comfyui", {}).get("launch_args", [])]
    enable_manager = os.environ.get("HYBRID_COMFYUI_ENABLE_MANAGER", "1").strip()
    if enable_manager == "0":
        default_launch_args = [arg for arg in default_launch_args if arg != "--enable-manager"]
    elif "--enable-manager" not in default_launch_args:
        default_launch_args.append("--enable-manager")

    for arg in default_launch_args:
        if arg not in forwarded_args:
            args.append(arg)

    use_cpu = os.environ.get("HYBRID_COMFYUI_USE_CPU", "0")
    if use_cpu.strip() == "1" and "--cpu" not in forwarded_args:
        args.append("--cpu")

    listen = os.environ.get("HYBRID_COMFYUI_LISTEN", str(settings.get("comfyui", {}).get("bind_address", "127.0.0.1")))
    requested_port = int(os.environ.get("HYBRID_COMFYUI_PORT", str(settings.get("comfyui", {}).get("port", 8188))))
    allocation = allocate_port(
        app_id=default_app_id(root),
        service_name="comfyui",
        preferred_port=requested_port,
        host=listen,
        allowed_range=default_port_range(requested_port),
        notes=str(root.resolve()),
    )
    port = allocation.assigned_port
    if listen and "--listen" not in forwarded_args:
        args.extend(["--listen", listen])
    if port and "--port" not in forwarded_args:
        args.extend(["--port", str(port)])
    if "--front-end-root" not in forwarded_args and "--front-end-root" not in default_launch_args:
        frontend_root = resolve_frontend_root(python_exe)
        if frontend_root is not None:
            args.extend(["--front-end-root", str(frontend_root)])

    args.extend(forwarded_args)

    tool_paths["runtime_dir"].mkdir(parents=True, exist_ok=True)
    print(f"Service comfyui bound to {listen}:{port}", flush=True)
    print(f"Launching ComfyUI with: {' '.join(args)} (cwd={comfy_dir})", flush=True)
    try:
        popen_kwargs: dict[str, object] = {
            "cwd": str(comfy_dir),
            "env": env,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(args, **popen_kwargs)
        record_reservation(
            app_id=default_app_id(root),
            service_name="comfyui",
            protocol="tcp",
            host=listen,
            requested_port=requested_port,
            assigned_port=port,
            pid=int(process.pid),
            notes=str(root.resolve()),
        )
        write_local_port_state(app_id=default_app_id(root), project_root=root)
        return process.wait()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
