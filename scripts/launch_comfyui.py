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


def resolve_python(root: Path) -> Path:
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python

    return Path(sys.executable)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    comfy_dir = root / "comfyui"
    main_py = comfy_dir / "main.py"
    python_exe = resolve_python(root)

    if not comfy_dir.exists() or not main_py.exists():
        print("ComfyUI repository not found under ./comfyui or missing main.py.")
        print("Make sure to clone https://github.com/comfyanonymous/ComfyUI into ./comfyui")
        return 2

    env = os.environ.copy()
    forwarded_args = sys.argv[1:]
    args = [str(python_exe), str(main_py)]

    enable_manager = os.environ.get("HYBRID_COMFYUI_ENABLE_MANAGER", "1").strip()
    if enable_manager != "0" and "--enable-manager" not in forwarded_args:
        args.append("--enable-manager")

    use_cpu = os.environ.get("HYBRID_COMFYUI_USE_CPU", "0")
    if use_cpu.strip() == "1" and "--cpu" not in forwarded_args:
        args.append("--cpu")

    listen = os.environ.get("HYBRID_COMFYUI_LISTEN")
    port = os.environ.get("HYBRID_COMFYUI_PORT")
    if listen and "--listen" not in forwarded_args:
        args.extend(["--listen", listen])
    if port and "--port" not in forwarded_args:
        args.extend(["--port", port])

    args.extend(forwarded_args)

    print(f"Launching ComfyUI with: {' '.join(args)} (cwd={comfy_dir})", flush=True)
    try:
        return subprocess.call(args, cwd=str(comfy_dir), env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
