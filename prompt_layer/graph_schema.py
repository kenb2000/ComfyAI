"""graph_schema.py

Adapter to convert the simple internal graph format into a ComfyUI prompt
payload suitable for submission to the API.

Internal graph format:
{
  "nodes": [ {"id": 1, "class_type": "KSampler", "inputs": {...}}, ... ],
  "edges": [ [src_id, src_port, dst_id, dst_port], ... ],
  "metadata": {...}
}

ComfyUI prompt format (simplified):
{
  "1": {"class_type": "KSampler", "inputs": {"model": ["10", "MODEL"], ...}},
  "10": {"class_type": "CheckpointLoaderSimple", "inputs": {...}},
  ...
}

We map edges so that dst.inputs[dst_port] = [str(src_id), src_port]
If an input already has a literal value, an edge overrides it.
"""
from __future__ import annotations

from typing import Dict, Any


def to_comfy_prompt(graph: Dict[str, Any]) -> Dict[str, Any]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build base mapping
    prompt: Dict[str, Any] = {}
    for n in nodes:
        nid = str(n.get("id"))
        prompt[nid] = {
            "class_type": n.get("class_type"),
            "inputs": dict(n.get("inputs", {})),
        }

    # Apply edges
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 4:
            continue
        src, src_port, dst, dst_port = edge
        dst_id = str(dst)
        if dst_id not in prompt:
            continue
        dst_inputs = prompt[dst_id].setdefault("inputs", {})
        dst_inputs[str(dst_port)] = [str(src), str(src_port)]

    return prompt
