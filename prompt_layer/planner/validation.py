"""Validation and bounded repair helpers for local workflow planning."""

from __future__ import annotations

from typing import Any


PLACEHOLDER_SENTINELS = {
    "CONTROL_IMAGE_PATH": "control_image",
    "INPUT_IMAGE_PATH": "input_image",
}

TEMPLATE_INPUT_REQUIREMENTS = {
    "controlnet_depth_upscale": ("control_image",),
    "img2img_lora": ("input_image",),
    "sdxl_base_refiner": (),
}


def normalize_object_info(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("nodes"), dict):
            return dict(payload["nodes"])
        if isinstance(payload.get("object_info"), dict):
            return dict(payload["object_info"])
        return {str(key): value for key, value in payload.items() if isinstance(key, str)}
    return {}


def available_node_types(payload: Any) -> set[str]:
    return set(normalize_object_info(payload))


def required_inputs_for_template(template_name: str) -> tuple[str, ...]:
    return TEMPLATE_INPUT_REQUIREMENTS.get(str(template_name), ())


def detect_missing_user_inputs(graph: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for node in list(graph.get("nodes", [])):
        inputs = dict(node.get("inputs", {}))
        class_type = str(node.get("class_type", ""))
        if class_type == "LoadImage":
            image_value = str(inputs.get("image", "") or "").strip()
            if image_value in PLACEHOLDER_SENTINELS:
                alias = PLACEHOLDER_SENTINELS[image_value]
                if alias not in missing:
                    missing.append(alias)
            elif not image_value:
                alias = "input_image"
                if alias not in missing:
                    missing.append(alias)
    return missing


def _invalid_edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    node_ids = {int(node.get("id")) for node in list(graph.get("nodes", [])) if isinstance(node.get("id"), int)}
    invalid: list[dict[str, Any]] = []
    for edge in list(graph.get("edges", [])):
        if not isinstance(edge, (list, tuple)) or len(edge) != 4:
            invalid.append({"edge": edge, "reason": "malformed"})
            continue
        src_id, src_port, dst_id, dst_port = edge
        if int(src_id) not in node_ids or int(dst_id) not in node_ids:
            invalid.append(
                {
                    "edge": [src_id, src_port, dst_id, dst_port],
                    "reason": "missing_node_reference",
                }
            )
    return invalid


def validate_graph(
    graph: dict[str, Any],
    object_info_payload: Any,
    *,
    template_name: str | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    catalog = normalize_object_info(object_info_payload)
    available = set(catalog)
    nodes = list(graph.get("nodes", []))

    if not nodes:
        issues.append("workflow_graph_has_no_nodes")

    if not available:
        issues.append("comfyui_object_info_unavailable")

    missing_node_types = sorted(
        {
            str(node.get("class_type"))
            for node in nodes
            if str(node.get("class_type")) and available and str(node.get("class_type")) not in available
        }
    )
    if missing_node_types:
        issues.append("missing_comfyui_node_types")

    invalid_edges = _invalid_edges(graph)
    if invalid_edges:
        issues.append("invalid_graph_edges")

    missing_inputs = detect_missing_user_inputs(graph)
    for alias in required_inputs_for_template(template_name or ""):
        if alias not in missing_inputs:
            continue
        issues.append(f"missing_required_input:{alias}")

    ok = not issues
    return {
        "ok": ok,
        "issues": issues,
        "issue_count": len(issues) + len(missing_node_types) + len(invalid_edges) + len(missing_inputs),
        "available_node_type_count": len(available),
        "missing_node_types": missing_node_types,
        "missing_inputs": missing_inputs,
        "invalid_edges": invalid_edges,
    }
