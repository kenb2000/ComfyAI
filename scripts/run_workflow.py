r"""Run a ComfyUI workflow by POSTing a prompt and optionally polling for results.

Assumes ComfyUI is running (default 127.0.0.1:8188). The script will:
- read a graph (internal or comfy prompt format),
- convert to comfy prompt if needed,
- POST to /prompt,
- optionally poll /history/<prompt_id> for completion and print output images paths
  if available in the response (depends on server settings).

Usage (PowerShell):
  C:/Users/Ken/Projects/ComfyUIhybrid/.venv/Scripts/python.exe .\scripts\run_workflow.py .\tests\out_graph.json --poll

"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import request


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=20) as rsp:
        return json.loads(rsp.read().decode("utf-8"))


def get_json(url: str) -> dict:
    with request.urlopen(url, timeout=10) as rsp:
        return json.loads(rsp.read().decode("utf-8"))


def to_prompt_if_needed(data: dict) -> dict:
    # ComfyUI prompt format has string keys per node id; our internal graph uses a dict with 'nodes' list.
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        from prompt_layer.graph_schema import to_comfy_prompt
        return to_comfy_prompt(data)
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("graph", help="Path to graph JSON (internal or comfy prompt)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--poll", action="store_true", help="Poll /history until status done")
    ap.add_argument("--timeout", type=int, default=120, help="Polling timeout seconds")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    prompt = to_prompt_if_needed(data)

    payload = {"prompt": prompt}
    url = f"http://{args.host}:{args.port}/prompt"

    try:
        rsp = post_json(url, payload)
    except Exception as e:
        print(f"POST /prompt failed: {e}")
        return 1

    prompt_id = rsp.get("prompt_id") or rsp.get("id") or rsp.get("promptId")
    print(f"Submitted prompt. Response keys: {list(rsp.keys())}")
    if prompt_id:
        print(f"Prompt ID: {prompt_id}")

    if not args.poll:
        return 0

    if not prompt_id:
        print("No prompt_id in response; cannot poll history.")
        return 0

    deadline = time.time() + args.timeout
    last_state = None

    while time.time() < deadline:
        try:
            hist = get_json(f"http://{args.host}:{args.port}/history/{prompt_id}")
        except Exception as e:
            print(f"GET /history/{prompt_id} failed: {e}")
            time.sleep(2)
            continue

        # Structure may vary; try to find a status indicator
        status = None
        if isinstance(hist, dict):
            status = hist.get("status") or hist.get("state") or hist.get("outputs")
        if status != last_state:
            print(f"History status update: {str(status)[:200]}")
            last_state = status

        # Heuristic: consider done if outputs exist and no running flag
        if isinstance(hist, dict) and hist.get("outputs"):
            print("Outputs present. Done.")
            # Try to extract any image paths if present
            outputs = hist["outputs"]
            images = []
            try:
                for node_id, node_out in outputs.items():
                    for k, v in node_out.items():
                        if isinstance(v, list):
                            for item in v:
                                if isinstance(item, dict) and "filename" in item:
                                    images.append(item["filename"])
            except Exception:
                pass
            if images:
                print("Output images:")
                for p in images[:10]:
                    print(" -", p)
            return 0

        time.sleep(2)

    print("Timeout waiting for workflow to complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
