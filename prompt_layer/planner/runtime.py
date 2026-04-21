"""Deterministic local planner runtime for ComfyAI.

Falcon 10B 1.58 remains the Linux-first baseline model identity because it is
the best throughput/concurrency tradeoff for an always-on helper. The v1 local
planner keeps execution deterministic so normal workflow preparation stays fast
and stable on CPU while the model contract, storage, and extension points are
already wired in for future on-device generation.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from ..graph_schema import to_comfy_prompt
from ..prompt_to_graph import PromptToGraph
from .config import (
    FALCON_DISPLAY_NAME,
    FALCON_MODEL_ID,
    build_local_planner_policy,
    build_local_planner_status,
    resolve_local_planner_model_path,
    resolve_planner_output_dir,
)
from .prompts import planner_prompt_bundle
from .validation import validate_graph


PlannerEmitter = Callable[[dict[str, Any]], None]

PROFILE_DEFAULTS = {
    "preview": {"steps": 18, "cfg": 6.0, "refiner_split": 10},
    "quality": {"steps": 30, "cfg": 7.0, "refiner_split": 14},
    "first_last_frame": {"steps": 24, "cfg": 6.5, "refiner_split": 12},
    "blender_guided": {"steps": 24, "cfg": 6.5, "refiner_split": 12},
}
TEMPLATE_PRIORITY = (
    "sdxl_base_refiner",
    "img2img_lora",
    "controlnet_depth_upscale",
)


class LocalPlannerError(RuntimeError):
    """Raised when the local planner cannot produce a valid plan."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)


def _stream_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event_type,
        "event": event_type,
        "timestamp": _now_iso(),
        "data": data,
    }


class LocalPlannerRuntime:
    """Prepare, validate, repair, and optionally queue ComfyUI workflows."""

    def __init__(self, settings: dict[str, Any], project_root: Path | str) -> None:
        self.settings = settings
        self.project_root = Path(project_root)
        self.prompt_to_graph = PromptToGraph()
        self.prompts = planner_prompt_bundle()

    def policy(self) -> dict[str, Any]:
        return build_local_planner_policy(self.settings, self.project_root)

    def status(self, *, object_info_payload: Any | None = None) -> dict[str, Any]:
        return build_local_planner_status(
            self.settings,
            self.project_root,
            object_info_ok=bool(object_info_payload),
        )

    def models(self) -> dict[str, Any]:
        policy = self.policy()
        model_path, _, inspection = resolve_local_planner_model_path(self.settings, self.project_root)
        return {
            "mode": policy["mode"],
            "default_model": {
                "id": policy["default_model_id"],
                "label": policy["display_name"],
                "path": str(model_path.resolve()) if model_path is not None else None,
                "present": bool(inspection.get("ok", False)),
            },
            "roles": dict(policy["role_mapping"]),
            "models": [
                {
                    "id": policy["default_model_id"],
                    "label": policy["display_name"],
                    "path": str(model_path.resolve()) if model_path is not None else None,
                    "present": bool(inspection.get("ok", False)),
                    "is_default": True,
                }
            ],
        }

    def dispatch(self, prompt: str) -> dict[str, Any]:
        template_name = self.prompt_to_graph.infer_intent(prompt)
        return {
            "prompt": prompt,
            "workflow_family": template_name,
            "selected_template": template_name,
            "reason": "template_keywords",
        }

    def rebuild(self) -> dict[str, Any]:
        policy = self.policy()
        output_dir = resolve_planner_output_dir(self.settings, self.project_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "ok": bool(policy["model_present"]),
            "planner": build_local_planner_status(
                self.settings,
                self.project_root,
                object_info_ok=bool(policy["last_verify_ok"]),
            ),
        }

    def verify(self, object_info_payload: Any, *, comfy_base_url: str | None = None) -> dict[str, Any]:
        dispatch = self.dispatch("Create SDXL base with refiner")
        result = self.plan(
            {
                "prompt": "Create SDXL base with refiner",
                "workflow_profile": "preview",
                "queue_workflow": False,
            },
            object_info_payload=object_info_payload,
            comfy_base_url=comfy_base_url,
            persist_artifact=False,
        )
        return {
            "ok": bool(result["ok"]),
            "dispatch_smoke_test": dispatch,
            "plan_smoke_test": {
                "selected_template": result["selected_template"],
                "validation": result["validation"],
                "repair_count": result["repair_count"],
                "artifact_path": result.get("artifact_path"),
            },
            "last_verify_at": _now_iso(),
            "summary": "Local planner validated successfully." if result["ok"] else "Local planner validation failed.",
        }

    def plan(
        self,
        payload: dict[str, Any],
        *,
        object_info_payload: Any,
        comfy_base_url: str | None = None,
        emit: PlannerEmitter | None = None,
        persist_artifact: bool = True,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        prompt = str(payload.get("prompt") or payload.get("request") or "").strip()
        if not prompt:
            raise LocalPlannerError("A prompt is required for local planning.")

        policy = self.policy()
        if not policy["model_present"]:
            raise LocalPlannerError("Falcon planner model is missing. Run setup acquire or planner rebuild first.")

        if emit is not None:
            emit(_stream_event("progress", {"stage": "dispatch", "prompt": prompt, "model_id": policy["default_model_id"]}))

        dispatch = self.dispatch(prompt)
        workflow_profile = str(payload.get("workflow_profile") or payload.get("profile") or "preview").strip() or "preview"
        overrides = self._build_overrides(payload, workflow_profile)

        if emit is not None:
            emit(
                _stream_event(
                    "progress",
                    {
                        "stage": "plan",
                        "selected_template": dispatch["selected_template"],
                        "workflow_profile": workflow_profile,
                    },
                )
            )

        template_name = dispatch["selected_template"]
        graph = self._render_graph(prompt, template_name, overrides, payload)
        validation = validate_graph(graph, object_info_payload, template_name=template_name)
        repairs: list[dict[str, Any]] = []
        max_repairs = int(policy["max_repairs_before_fail"])
        repair_count = 0

        while not validation["ok"] and repair_count < max_repairs:
            if emit is not None:
                emit(
                    _stream_event(
                        "progress",
                        {
                            "stage": "validate",
                            "selected_template": template_name,
                            "repair_count": repair_count,
                            "issues": validation["issues"],
                        },
                    )
                )
            repaired = self._repair_graph(
                prompt,
                current_template=template_name,
                current_validation=validation,
                overrides=overrides,
                payload=payload,
                object_info_payload=object_info_payload,
            )
            if repaired is None:
                break
            template_name, graph, repair_detail = repaired
            repairs.append(repair_detail)
            repair_count += 1
            validation = validate_graph(graph, object_info_payload, template_name=template_name)

        if emit is not None:
            emit(
                _stream_event(
                    "progress",
                    {
                        "stage": "validate",
                        "selected_template": template_name,
                        "repair_count": repair_count,
                        "issues": validation["issues"],
                        "ok": validation["ok"],
                    },
                )
            )

        comfy_prompt = to_comfy_prompt(graph) if validation["ok"] else None
        queue_result = None
        if validation["ok"] and payload.get("queue_workflow") and comfy_base_url:
            queue_result = self._queue_workflow(comfy_base_url, comfy_prompt)

        artifact_path = None
        if validation["ok"] and persist_artifact:
            artifact_path = self._write_plan_artifact(
                {
                    "selected_template": template_name,
                    "workflow_profile": workflow_profile,
                    "parameters": overrides,
                    "validation": validation,
                    "workflow_json": graph,
                    "comfy_prompt": comfy_prompt,
                }
            )

        elapsed = time.perf_counter() - started
        result = {
            "ok": bool(validation["ok"]),
            "mode": policy["mode"],
            "selected_template": template_name,
            "workflow_family": dispatch["workflow_family"],
            "parameters": overrides,
            "workflow_json": graph,
            "comfy_prompt": comfy_prompt,
            "validation": validation,
            "repair_count": repair_count,
            "repairs": repairs,
            "queue": queue_result,
            "timing": {
                "total_seconds": round(elapsed, 4),
            },
            "model": {
                "display_name": policy["display_name"],
                "model_id": policy["default_model_id"],
                "role_mapping": dict(policy["role_mapping"]),
            },
            "artifact_path": str(artifact_path.resolve()) if artifact_path is not None else None,
            "prompts": dict(self.prompts),
        }

        if not result["ok"]:
            raise LocalPlannerError(
                "Local planner validation failed after bounded repairs: "
                + ", ".join(validation["issues"] or ["unknown_error"])
            )

        if emit is not None:
            emit(_stream_event("done", result))
        return result

    def _build_overrides(self, payload: dict[str, Any], workflow_profile: str) -> dict[str, Any]:
        profile_defaults = PROFILE_DEFAULTS.get(workflow_profile, PROFILE_DEFAULTS["preview"])
        width = payload.get("width")
        height = payload.get("height")
        overrides: dict[str, Any] = {
            "steps": int(payload.get("steps") or profile_defaults["steps"]),
            "cfg": float(payload.get("cfg") or profile_defaults["cfg"]),
            "refiner_split": int(payload.get("refiner_split") or profile_defaults["refiner_split"]),
        }
        if width and height:
            overrides["size"] = f"{int(width)}x{int(height)}"
        if payload.get("seed") is not None:
            overrides["seed"] = int(payload["seed"])
        if payload.get("sampler"):
            overrides["sampler"] = str(payload["sampler"])
        if payload.get("scheduler"):
            overrides["scheduler"] = str(payload["scheduler"])
        if payload.get("input_image"):
            overrides["input_image"] = str(payload["input_image"])
        if payload.get("control_image"):
            overrides["control_image"] = str(payload["control_image"])
        if payload.get("checkpoint_name"):
            overrides["checkpoint_name"] = str(payload["checkpoint_name"])
        if payload.get("controlnet_name"):
            overrides["controlnet_name"] = str(payload["controlnet_name"])
        if payload.get("lora_name"):
            overrides["lora_name"] = str(payload["lora_name"])
        return overrides

    def _render_graph(
        self,
        prompt: str,
        template_name: str,
        overrides: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        graph = self.prompt_to_graph.resolver.load(template_name)
        graph = self.prompt_to_graph.parameterize(graph, prompt)
        graph = self.prompt_to_graph.apply_overrides(graph, overrides)
        for node in list(graph.get("nodes", [])):
            inputs = node.setdefault("inputs", {})
            class_type = str(node.get("class_type", ""))
            if class_type == "CheckpointLoaderSimple" and overrides.get("checkpoint_name"):
                inputs["ckpt_name"] = overrides["checkpoint_name"]
            if class_type == "ControlNetLoader" and overrides.get("controlnet_name"):
                inputs["control_net_name"] = overrides["controlnet_name"]
            if class_type == "LoraLoader" and overrides.get("lora_name"):
                inputs["lora_name"] = overrides["lora_name"]
            if class_type == "SaveImage":
                inputs.setdefault("filename_prefix", "ComfyAI")

        metadata = graph.setdefault("metadata", {})
        metadata["planner_mode"] = "local"
        metadata["planner_model_id"] = self.settings.get("planner", {}).get("default_model_id", FALCON_MODEL_ID)
        metadata["planner_model_label"] = self.settings.get("planner", {}).get("display_name", FALCON_DISPLAY_NAME)
        metadata["workflow_profile"] = str(payload.get("workflow_profile") or payload.get("profile") or "preview")
        metadata["planner_request_id"] = str(uuid.uuid4())
        return graph

    def _candidate_templates(self, prompt: str, current_template: str) -> list[str]:
        ranked = [
            self.prompt_to_graph.infer_intent(prompt),
            current_template,
            *TEMPLATE_PRIORITY,
        ]
        items: list[str] = []
        for item in ranked:
            if item not in items:
                items.append(item)
        return items

    def _repair_graph(
        self,
        prompt: str,
        *,
        current_template: str,
        current_validation: dict[str, Any],
        overrides: dict[str, Any],
        payload: dict[str, Any],
        object_info_payload: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        current_issue_count = int(current_validation.get("issue_count", 999999))
        for template_name in self._candidate_templates(prompt, current_template):
            if template_name == current_template:
                continue
            graph = self._render_graph(prompt, template_name, overrides, payload)
            validation = validate_graph(graph, object_info_payload, template_name=template_name)
            if validation["ok"] or validation["issue_count"] < current_issue_count:
                return (
                    template_name,
                    graph,
                    {
                        "from_template": current_template,
                        "to_template": template_name,
                        "reason": current_validation["issues"],
                    },
                )
        return None

    def _write_plan_artifact(self, payload: dict[str, Any]) -> Path:
        output_dir = resolve_planner_output_dir(self.settings, self.project_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = output_dir / f"planner-plan-{timestamp}.json"
        path.write_text(_canonical_json(payload), encoding="utf-8")
        return path

    def _queue_workflow(self, comfy_base_url: str, comfy_prompt: dict[str, Any]) -> dict[str, Any]:
        url = f"{str(comfy_base_url).rstrip('/')}/prompt"
        body = json.dumps({"prompt": comfy_prompt, "client_id": "comfyai-local-planner"}).encode("utf-8")
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=float(self.settings.get("planner", {}).get("request_timeout_seconds", 30))) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
            return {"ok": True, "url": url, "response": payload}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "url": url, "status_code": exc.code, "detail": detail}
        except Exception as exc:
            return {"ok": False, "url": url, "detail": str(exc)}
