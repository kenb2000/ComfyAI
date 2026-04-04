r"""Validate a generated ComfyUI-style graph JSON for basic structure.

Usage (PowerShell):

    python .\scripts\validate_graph.py .\tests\out_graph.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Set
import os
import sys


def _schema_from_comfy() -> Dict[str, Set[str]]:
    """Load node class names and their input fields from comfyui.nodes.

    Returns a mapping: class_type -> set(input_names). If a node doesn't expose
    INPUT_TYPES or required fields, it's omitted from the map (we'll skip checks).
    """
    schema: Dict[str, Set[str]] = {}
    try:
        # Ensure comfyui/ is importable when running from project root
        here = Path(__file__).resolve().parent.parent
        comfy_path = here / "comfyui"
        if comfy_path.exists():
            sys.path.insert(0, str(comfy_path))
        import nodes as comfy_nodes  # type: ignore
        mappings = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {})
        for name, cls in mappings.items():
            try:
                inputs = getattr(cls, "INPUT_TYPES", None)
                if inputs is None:
                    continue
                it = inputs()
                req = set(it.get("required", {}).keys())
                opt = set(it.get("optional", {}).keys()) if isinstance(it.get("optional"), dict) else set()
                schema[name] = req | opt
            except Exception:
                continue
    except Exception:
        pass
    return schema


def _validate_internal_graph(data: Dict[str, Any]) -> bool:
    ok = True
    schema = _schema_from_comfy()
    # Basic keys
    for key in ("nodes", "edges", "metadata"):
        if key not in data:
            print(f"Missing key: {key}")
            ok = False

    # Node id index
    idset = set()
    if ok:
        for n in data.get("nodes", []):
            if "id" not in n or "class_type" not in n:
                print(f"Node missing id or class_type: {n}")
                ok = False
            else:
                idset.add(n["id"])

    # Edge endpoints exist and input names look valid (best-effort)
    if ok:
        for e in data.get("edges", []):
            if not (isinstance(e, (list, tuple)) and len(e) == 4):
                print(f"Bad edge shape: {e}")
                ok = False
                continue
            src, _, dst, dst_port = e
            if src not in idset or dst not in idset:
                print(f"Edge references unknown nodes: {e}")
                ok = False
            # Check dst port plausibility using comfy schema if available
            try:
                dst_node = next(n for n in data.get("nodes", []) if n.get("id") == dst)
                cls_name = dst_node.get("class_type")
                if cls_name in schema:
                    valid_inputs = schema[cls_name]
                    if str(dst_port) not in valid_inputs:
                        print(f"Warning: input '{dst_port}' not in inputs of {cls_name}: {sorted(valid_inputs)}")
            except StopIteration:
                pass

    # Class types exist in comfy mapping (best-effort)
    if ok and schema:
        for n in data.get("nodes", []):
            ct = n.get("class_type")
            if ct not in schema:
                print(f"Warning: class_type '{ct}' not found in comfy NODE_CLASS_MAPPINGS.")
    return ok


def validate(path: Path) -> int:
    if not path.exists():
        print(f"File not found: {path}")
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to parse JSON: {e}")
        return 3

    if _validate_internal_graph(data):
        print("OK: graph structure looks valid.")
        return 0
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: validate_graph.py <path-to-graph.json>")
        sys.exit(2)
    sys.exit(validate(Path(sys.argv[1])))
