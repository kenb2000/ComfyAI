r"""Open a generated workflow in a running ComfyUI instance via its HTTP API.

This script assumes ComfyUI server is running (default 127.0.0.1:8188).
It converts the internal graph schema to the ComfyUI prompt format and POSTs it
so the front-end can display it or load it. Endpoint behavior may change across
versions; treat this as a starting point.

Usage (PowerShell):
  C:/Users/Ken/Projects/ComfyUIhybrid/.venv/Scripts/python.exe .\scripts\open_in_comfyui.py .\tests\out_graph.json

"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib import request


def post_json(url: str, payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as rsp:
        return rsp.read().decode("utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: open_in_comfyui.py <graph.json> [host] [port]")
        return 2
    path = Path(sys.argv[1])
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8188

    data = json.loads(path.read_text(encoding="utf-8"))

    # Convert to prompt format if not already
    if not all(isinstance(k, str) for k in data.keys()):
        from prompt_layer.graph_schema import to_comfy_prompt
        data = to_comfy_prompt(data)

    payload = {"prompt": data}

    # NOTE: This endpoint is indicative; ComfyUI may expose different routes for import.
    url = f"http://{host}:{port}/prompt"
    try:
        resp = post_json(url, payload)
        print("Posted workflow to ComfyUI /prompt endpoint.")
        print(resp[:500])
    except Exception as e:
        print(f"Failed to post to ComfyUI API: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
