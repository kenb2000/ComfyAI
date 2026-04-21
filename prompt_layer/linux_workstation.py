"""Linux workstation capability, planning, and benchmark helpers."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CHECKPOINT_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
LTX_NODE_FALLBACKS = (
    "comfyui/custom_nodes/ComfyUI-LTXVideo",
    "comfyui/custom_nodes/LTXVideo",
    "comfyui/custom_nodes/ltxvideo",
)
MODEL_SEARCH_DIRS = ("checkpoints", "diffusion_models", "unet")
ENV_TRUE = {"1", "true", "yes", "on", "enabled"}
ENV_FALSE = {"0", "false", "no", "off", "disabled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy_flag(value: str) -> bool | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in ENV_TRUE:
        return True
    if normalized in ENV_FALSE:
        return False
    return None


def _read_env_flag(names: list[str]) -> tuple[bool | None, str | None]:
    for name in names:
        raw = os.environ.get(name, "")
        parsed = _truthy_flag(raw)
        if parsed is not None:
            return parsed, name
    return None, None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    parsed = _truthy_flag(str(value))
    return default if parsed is None else parsed


def _canonical_relative(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _iter_text_tokens(value: Any, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value.lower()]
    if isinstance(value, dict):
        tokens: list[str] = []
        for key, child in value.items():
            tokens.append(str(key).lower())
            tokens.extend(_iter_text_tokens(child, depth + 1))
        return tokens
    if isinstance(value, list):
        tokens = []
        for child in value[:64]:
            tokens.extend(_iter_text_tokens(child, depth + 1))
        return tokens
    if value is None:
        return []
    return [str(value).lower()]


def _payload_has_any_term(payload: Any, terms: tuple[str, ...]) -> bool:
    if payload is None:
        return False
    tokens = _iter_text_tokens(payload)
    return any(term in token for token in tokens for term in terms)


def _detect_nvidia_gpu() -> dict[str, Any]:
    fallback_present = Path("/dev/nvidiactl").exists() or any(Path("/dev").glob("nvidia*"))
    command = shutil.which("nvidia-smi")
    if command is None:
        return {
            "present": bool(fallback_present),
            "source": "device_nodes" if fallback_present else "not_detected",
            "name": None,
            "driver_version": None,
            "total_memory_mb": None,
            "raw": None,
        }

    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {
            "present": True,
            "source": "nvidia_smi_error",
            "name": None,
            "driver_version": None,
            "total_memory_mb": None,
            "raw": str(exc),
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "nvidia-smi failed"
        return {
            "present": True,
            "source": "nvidia_smi_error",
            "name": None,
            "driver_version": None,
            "total_memory_mb": None,
            "raw": detail,
        }

    line = next((item.strip() for item in result.stdout.splitlines() if item.strip()), "")
    parts = [item.strip() for item in line.split(",")]
    total_memory_mb = _safe_int(parts[2], 0) if len(parts) >= 3 else 0
    return {
        "present": True,
        "source": "nvidia_smi",
        "name": parts[0] if parts else None,
        "driver_version": parts[1] if len(parts) >= 2 else None,
        "total_memory_mb": total_memory_mb or None,
        "raw": line,
    }


def _classify_checkpoint_file(path: Path, project_root: Path, models_root: Path) -> dict[str, Any]:
    name = path.name.lower()
    relative = _canonical_relative(project_root, path)
    relative_to_models = _canonical_relative(models_root, path) if path.is_relative_to(models_root) else relative
    ltx_terms = ("ltx", "ltxv", "ltx-video")
    fp8_terms = ("fp8",)
    distilled_terms = ("distill", "distilled")
    dev_terms = ("-dev", "_dev", " dev", "development")
    return {
        "name": path.name,
        "absolute_path": str(path.resolve()),
        "relative_path": relative,
        "relative_to_models": relative_to_models,
        "size_bytes": path.stat().st_size,
        "is_ltx": any(term in name for term in ltx_terms),
        "is_ltx_23": "ltx" in name and any(term in name for term in ("2.3", "2_3", "23")),
        "is_fp8": any(term in name for term in fp8_terms),
        "is_distilled": any(term in name for term in distilled_terms),
        "is_dev": any(term in name for term in dev_terms),
        "is_nvfp4": "nvfp4" in name,
    }


def scan_checkpoint_inventory(project_root: Path, comfy_root: Path) -> dict[str, Any]:
    models_root = comfy_root / "models"
    items: list[dict[str, Any]] = []
    for directory in MODEL_SEARCH_DIRS:
        root = models_root / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in CHECKPOINT_SUFFIXES:
                continue
            items.append(_classify_checkpoint_file(path, project_root, models_root))

    ltx_items = [item for item in items if item["is_ltx"]]
    fp8_items = [item for item in items if item["is_fp8"]]
    ltx_fp8_items = [item for item in items if item["is_ltx"] and item["is_fp8"]]
    ltx_distilled_items = [item for item in items if item["is_ltx"] and item["is_distilled"]]
    ltx_dev_items = [item for item in items if item["is_ltx"] and item["is_dev"]]

    return {
        "models_root": str(models_root.resolve()),
        "count": len(items),
        "ltx_available": bool(ltx_items),
        "ltx_23_available": any(item["is_ltx_23"] for item in items),
        "fp8_available": bool(fp8_items),
        "ltx_fp8_available": bool(ltx_fp8_items),
        "ltx_distilled_available": bool(ltx_distilled_items),
        "ltx_dev_available": bool(ltx_dev_items),
        "non_fp8_available": any(not item["is_fp8"] for item in items),
        "nvfp4_available": any(item["is_nvfp4"] for item in items),
        "preferred_candidates": {
            "ltx_fp8": [item["relative_to_models"] for item in ltx_fp8_items[:5]],
            "ltx_distilled": [item["relative_to_models"] for item in ltx_distilled_items[:5]],
            "ltx_dev": [item["relative_to_models"] for item in ltx_dev_items[:5]],
        },
        "items": items[:128],
    }


def scan_ltx_node_state(settings: dict[str, Any], project_root: Path, comfy_root: Path) -> dict[str, Any]:
    linux_settings = settings.get("linux_workstation", {})
    node_settings = linux_settings.get("ltx_nodes", {})
    configured_candidates = node_settings.get("candidate_paths", [])
    candidate_paths = list(configured_candidates) if configured_candidates else list(LTX_NODE_FALLBACKS)

    discovered: list[dict[str, Any]] = []
    for raw_path in candidate_paths:
        candidate = Path(str(raw_path))
        resolved = candidate if candidate.is_absolute() else project_root / candidate
        discovered.append(
            {
                "configured_path": str(raw_path),
                "absolute_path": str(resolved.resolve()),
                "exists": resolved.exists(),
            }
        )

    if not any(item["exists"] for item in discovered):
        custom_nodes_root = comfy_root / "custom_nodes"
        if custom_nodes_root.exists():
            for child in sorted(custom_nodes_root.iterdir()):
                if child.is_dir() and "ltx" in child.name.lower():
                    discovered.append(
                        {
                            "configured_path": _canonical_relative(project_root, child),
                            "absolute_path": str(child.resolve()),
                            "exists": True,
                        }
                    )

    return {
        "required": bool(node_settings.get("required", True)),
        "available": any(item["exists"] for item in discovered),
        "candidates": discovered,
        "source": node_settings.get("source", {}),
    }


def _select_model_variant(capabilities: dict[str, Any], requested_variant: str | None = None) -> dict[str, Any]:
    inventory = capabilities.get("checkpoint_inventory", {})
    requested = str(requested_variant or "").strip().lower()

    if requested == "nvfp4":
        requested = ""

    if requested == "fp8" and inventory.get("ltx_fp8_available"):
        return {"label": "fp8", "model_family": "ltx-2.3", "source": "explicit_request"}
    if requested == "distilled" and inventory.get("ltx_distilled_available"):
        return {"label": "distilled", "model_family": "ltx-2.3", "source": "explicit_request"}
    if requested == "dev" and inventory.get("ltx_dev_available"):
        return {"label": "dev", "model_family": "ltx-2.3", "source": "explicit_request"}
    if requested == "non_fp8" and inventory.get("non_fp8_available"):
        return {"label": "non_fp8", "model_family": "ltx-2.3" if inventory.get("ltx_available") else "generic", "source": "explicit_request"}

    if inventory.get("ltx_fp8_available"):
        return {"label": "fp8", "model_family": "ltx-2.3", "source": "inventory_preference"}
    if inventory.get("ltx_distilled_available"):
        return {"label": "distilled", "model_family": "ltx-2.3", "source": "inventory_preference"}
    if inventory.get("ltx_dev_available"):
        return {"label": "dev", "model_family": "ltx-2.3", "source": "inventory_preference"}
    if inventory.get("ltx_available") and inventory.get("non_fp8_available"):
        return {"label": "non_fp8", "model_family": "ltx-2.3", "source": "inventory_fallback"}
    if inventory.get("non_fp8_available"):
        return {"label": "non_fp8", "model_family": "generic", "source": "inventory_fallback"}
    return {"label": "none", "model_family": "unavailable", "source": "missing"}


def default_linux_workstation_settings(project_root: Path) -> dict[str, Any]:
    verification_dir = project_root / "tools" / "runtime" / "verification" / "linux"
    benchmark_dir = project_root / "tools" / "runtime" / "benchmarks" / "linux"
    return {
        "enabled": True,
        "role": "stable_workstation_development_node",
        "role_label": "stable workstation / development node",
        "machine_label": "Linux stable local generation and fallback node",
        "active_profile": "linux_stable_nvidia",
        "ltx_nodes": {
            "required": True,
            "candidate_paths": list(LTX_NODE_FALLBACKS),
            "source": {"kind": "git_url", "value": ""},
        },
        "artifacts": {
            "verification_dir": _canonical_relative(project_root, verification_dir),
            "benchmark_dir": _canonical_relative(project_root, benchmark_dir),
            "latest_verification": _canonical_relative(project_root, verification_dir / "latest.json"),
            "latest_benchmark": _canonical_relative(project_root, benchmark_dir / "latest.json"),
        },
        "profiles": {
            "linux_stable_nvidia": {
                "name": "linux_stable_nvidia",
                "prefer_ltx_23": True,
                "prefer_fp8": True,
                "prefer_distilled_fallback": True,
                "assume_blackwell_only_features": False,
                "nvfp4_supported": False,
                "default_workflow_profile": "preview",
                "optimizations": {
                    "async_offload": {"requested": True},
                    "pinned_memory": {"requested": True},
                    "weight_streaming": {"mode": "auto"},
                },
                "resource_caps": {
                    "default_concurrency": 1,
                    "max_background_jobs": 1,
                    "low_workstation_impact_mode": True,
                    "memory_warning_threshold_mb": 16384,
                    "memory_hard_threshold_mb": 20480,
                    "runtime_warning_threshold_seconds": 720,
                    "runtime_hard_threshold_seconds": 1500,
                    "weight_streaming_trigger_mb": 17000,
                    "weight_streaming_headroom_mb": 6144,
                },
                "workflow_profiles": {
                    "preview": {
                        "label": "Preview",
                        "description": "Local iteration and development with stable memory behavior.",
                        "width": 1024,
                        "height": 576,
                        "frames": 33,
                        "fps": 12,
                        "variant_preference": ["fp8", "distilled", "dev", "non_fp8"],
                    },
                    "quality": {
                        "label": "Quality",
                        "description": "Conservative quality mode that keeps the workstation responsive.",
                        "width": 1280,
                        "height": 720,
                        "frames": 49,
                        "fps": 16,
                        "variant_preference": ["fp8", "dev", "distilled", "non_fp8"],
                    },
                    "first_last_frame": {
                        "label": "FirstFrame/LastFrame",
                        "description": "Safe defaults for guided first-frame and last-frame interpolation.",
                        "width": 1024,
                        "height": 576,
                        "frames": 41,
                        "fps": 12,
                        "variant_preference": ["fp8", "distilled", "dev", "non_fp8"],
                    },
                    "blender_guided": {
                        "label": "Blender Guided",
                        "description": "Optional advanced workflow that expects Blender guidance assets.",
                        "width": 1280,
                        "height": 720,
                        "frames": 49,
                        "fps": 16,
                        "requires_blender": True,
                        "variant_preference": ["fp8", "distilled", "dev", "non_fp8"],
                    },
                },
            }
        },
    }


def load_artifact_summary(project_root: Path, raw_path: str | None) -> dict[str, Any] | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    resolved = path if path.is_absolute() else project_root / path
    if not resolved.exists():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def detect_linux_workstation_capabilities(
    settings: dict[str, Any],
    project_root: Path,
    comfy_root: Path,
    object_info_payload: Any | None = None,
) -> dict[str, Any]:
    linux_settings = settings.get("linux_workstation", {})
    profiles = linux_settings.get("profiles", {})
    active_profile_name = str(linux_settings.get("active_profile", "linux_stable_nvidia"))
    active_profile = profiles.get(active_profile_name, {})
    optimizations = active_profile.get("optimizations", {})
    resource_caps = active_profile.get("resource_caps", {})

    running_on_linux = platform.system().lower() == "linux"
    nvidia = _detect_nvidia_gpu()
    ltx_nodes = scan_ltx_node_state(settings, project_root, comfy_root)
    checkpoint_inventory = scan_checkpoint_inventory(project_root, comfy_root)

    async_override, async_override_source = _read_env_flag([
        "COMFYAI_ASYNC_OFFLOAD",
        "COMFYUI_ASYNC_OFFLOAD",
        "LTX_ASYNC_OFFLOAD",
    ])
    pinned_override, pinned_override_source = _read_env_flag([
        "COMFYAI_PINNED_MEMORY",
        "COMFYUI_PINNED_MEMORY",
        "LTX_PINNED_MEMORY",
    ])
    weight_override, weight_override_source = _read_env_flag([
        "COMFYAI_WEIGHT_STREAMING",
        "COMFYUI_WEIGHT_STREAMING",
        "LTX_WEIGHT_STREAMING",
    ])
    nvfp4_override, nvfp4_override_source = _read_env_flag([
        "COMFYAI_NVFP4_SUPPORTED",
        "COMFYUI_NVFP4_SUPPORTED",
        "NVIDIA_NVFP4_SUPPORTED",
    ])

    async_available = running_on_linux and bool(nvidia.get("present"))
    pinned_available = running_on_linux and bool(nvidia.get("present"))
    weight_available = running_on_linux and bool(nvidia.get("present")) and (
        checkpoint_inventory.get("ltx_available") or ltx_nodes.get("available")
    )

    async_enabled = bool(optimizations.get("async_offload", {}).get("requested", True)) and async_available
    if async_override is not None:
        async_enabled = async_available and async_override

    pinned_enabled = bool(optimizations.get("pinned_memory", {}).get("requested", True)) and pinned_available
    if pinned_override is not None:
        pinned_enabled = pinned_available and pinned_override

    weight_mode = str(optimizations.get("weight_streaming", {}).get("mode", "auto")).strip() or "auto"
    weight_enabled = weight_mode == "always" and weight_available
    if weight_override is not None:
        weight_enabled = weight_available and weight_override

    object_info_ltx = _payload_has_any_term(object_info_payload, ("ltx", "ltxvideo"))
    object_info_streaming = _payload_has_any_term(object_info_payload, ("stream", "streaming"))
    object_info_offload = _payload_has_any_term(object_info_payload, ("offload", "async offload"))
    object_info_pinned = _payload_has_any_term(object_info_payload, ("pinned", "pin_memory", "pin memory"))
    object_info_nvfp4 = _payload_has_any_term(object_info_payload, ("nvfp4",))

    if object_info_ltx:
        ltx_nodes["available"] = True
    if object_info_streaming:
        weight_available = True
    if object_info_offload:
        async_available = True
    if object_info_pinned:
        pinned_available = True

    nvfp4_supported = bool(nvfp4_override) if nvfp4_override is not None else bool(object_info_nvfp4)
    blender_path = shutil.which("blender") or os.environ.get("BLENDER_PATH", "") or ""

    artifacts = linux_settings.get("artifacts", {})
    latest_verification = load_artifact_summary(project_root, artifacts.get("latest_verification"))
    latest_benchmark = load_artifact_summary(project_root, artifacts.get("latest_benchmark"))

    return {
        "enabled": bool(linux_settings.get("enabled", True)),
        "running_on_linux": running_on_linux,
        "role": linux_settings.get("role", "stable_workstation_development_node"),
        "role_label": linux_settings.get("role_label", "stable workstation / development node"),
        "machine_label": linux_settings.get("machine_label", "Linux stable local generation and fallback node"),
        "active_profile": active_profile_name,
        "profile": active_profile,
        "nvidia_gpu": nvidia,
        "ltx_nodes": ltx_nodes,
        "checkpoint_inventory": checkpoint_inventory,
        "capabilities": {
            "nvidia_gpu_present": bool(nvidia.get("present")),
            "ltx_video_node_available": bool(ltx_nodes.get("available")),
            "fp8_capable_checkpoints_available": bool(checkpoint_inventory.get("fp8_available")),
            "ltx_23_checkpoints_available": bool(checkpoint_inventory.get("ltx_23_available")),
            "blender_present": bool(blender_path),
            "nvfp4_supported": nvfp4_supported,
        },
        "optimizations": {
            "async_offload": {
                "requested": bool(optimizations.get("async_offload", {}).get("requested", True)),
                "available": async_available,
                "enabled": async_enabled,
                "source": async_override_source or ("object_info" if object_info_offload else "profile_default"),
            },
            "pinned_memory": {
                "requested": bool(optimizations.get("pinned_memory", {}).get("requested", True)),
                "available": pinned_available,
                "enabled": pinned_enabled,
                "source": pinned_override_source or ("object_info" if object_info_pinned else "profile_default"),
            },
            "weight_streaming": {
                "requested_mode": weight_mode,
                "available": weight_available,
                "enabled": weight_enabled,
                "source": weight_override_source or ("object_info" if object_info_streaming else "profile_default"),
            },
            "nvfp4": {
                "available": nvfp4_supported,
                "enabled": False,
                "source": nvfp4_override_source or ("object_info" if object_info_nvfp4 else "not_reported"),
            },
        },
        "resource_caps": resource_caps,
        "blender": {
            "present": bool(blender_path),
            "path": blender_path or None,
        },
        "artifacts": artifacts,
        "latest_verification": latest_verification,
        "latest_benchmark": latest_benchmark,
        "detected_at": _now_iso(),
    }


def _estimate_plan_metrics(
    width: int,
    height: int,
    frames: int,
    effective_profile: str,
    variant_label: str,
    use_async_offload: bool,
    use_pinned_memory: bool,
    use_weight_streaming: bool,
    low_workstation_impact: bool,
) -> tuple[int, int]:
    pixels = max(width, 1) * max(height, 1)
    profile_memory_base = {
        "preview": 5600,
        "quality": 9400,
        "first_last_frame": 7000,
        "blender_guided": 9800,
    }.get(effective_profile, 6200)
    profile_runtime_base = {
        "preview": 140,
        "quality": 420,
        "first_last_frame": 240,
        "blender_guided": 520,
    }.get(effective_profile, 180)

    estimated_vram_mb = profile_memory_base + int(pixels / 180) + frames * 34
    estimated_runtime_seconds = profile_runtime_base + int(pixels / 6400) + frames * 7

    if variant_label == "fp8":
        estimated_vram_mb -= 2200
        estimated_runtime_seconds -= 70
    elif variant_label == "distilled":
        estimated_vram_mb -= 1400
        estimated_runtime_seconds -= 40
    elif variant_label == "dev":
        estimated_runtime_seconds += 20

    if use_async_offload:
        estimated_vram_mb -= 1200
        estimated_runtime_seconds += 20
    if use_pinned_memory:
        estimated_vram_mb -= 400
        estimated_runtime_seconds -= 10
    if use_weight_streaming:
        estimated_vram_mb -= 2200
        estimated_runtime_seconds += 60
    if low_workstation_impact:
        estimated_runtime_seconds += 35

    return max(estimated_vram_mb, 2048), max(estimated_runtime_seconds, 30)


def build_linux_runtime_plan(
    settings: dict[str, Any],
    capabilities: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = request_payload or {}
    active_profile = capabilities.get("profile", {})
    workflow_profiles = active_profile.get("workflow_profiles", {})
    resource_caps = capabilities.get("resource_caps", {})

    requested_profile = str(
        payload.get("workflow_profile")
        or payload.get("profile")
        or active_profile.get("default_workflow_profile", "preview")
    ).strip() or "preview"
    if requested_profile not in workflow_profiles:
        requested_profile = active_profile.get("default_workflow_profile", "preview")

    requested_variant = str(payload.get("model_variant") or payload.get("variant") or "").strip().lower() or None
    low_workstation_impact = _safe_bool(
        payload.get("low_workstation_impact"),
        bool(resource_caps.get("low_workstation_impact_mode", True)),
    )
    warnings: list[str] = []
    notes: list[str] = []

    effective_profile = requested_profile
    workflow_config = workflow_profiles.get(effective_profile, workflow_profiles.get("preview", {}))
    if workflow_config.get("requires_blender") and not capabilities.get("blender", {}).get("present"):
        warnings.append("Blender-guided workflow requested but Blender was not detected; downgrading to preview profile.")
        effective_profile = "preview"
        workflow_config = workflow_profiles.get(effective_profile, workflow_config)

    width = _safe_int(payload.get("width"), workflow_config.get("width", 1024))
    height = _safe_int(payload.get("height"), workflow_config.get("height", 576))
    frames = _safe_int(payload.get("frames"), workflow_config.get("frames", 33))
    fps = _safe_int(payload.get("fps"), workflow_config.get("fps", 12))

    variant = _select_model_variant(capabilities, requested_variant=requested_variant)
    if requested_variant == "nvfp4" or capabilities.get("optimizations", {}).get("nvfp4", {}).get("available") is False:
        if requested_variant == "nvfp4":
            warnings.append("NVFP4 was requested, but this Linux workstation does not report NVFP4 support; using a stable fallback variant instead.")

    async_state = capabilities.get("optimizations", {}).get("async_offload", {})
    pinned_state = capabilities.get("optimizations", {}).get("pinned_memory", {})
    weight_state = capabilities.get("optimizations", {}).get("weight_streaming", {})

    use_async_offload = bool(async_state.get("enabled", False))
    use_pinned_memory = bool(pinned_state.get("enabled", False))

    requested_weight = payload.get("weight_streaming")
    if isinstance(requested_weight, bool):
        use_weight_streaming = bool(weight_state.get("available", False)) and requested_weight
    elif str(requested_weight).strip().lower() == "always":
        use_weight_streaming = bool(weight_state.get("available", False))
    else:
        use_weight_streaming = bool(weight_state.get("enabled", False))

    estimated_vram_mb, estimated_runtime_seconds = _estimate_plan_metrics(
        width=width,
        height=height,
        frames=frames,
        effective_profile=effective_profile,
        variant_label=variant["label"],
        use_async_offload=use_async_offload,
        use_pinned_memory=use_pinned_memory,
        use_weight_streaming=use_weight_streaming,
        low_workstation_impact=low_workstation_impact,
    )

    weight_streaming_trigger = _safe_int(resource_caps.get("weight_streaming_trigger_mb"), 17000)
    total_memory_mb = capabilities.get("nvidia_gpu", {}).get("total_memory_mb")
    if weight_state.get("available") and not use_weight_streaming:
        low_headroom = total_memory_mb is not None and (total_memory_mb - estimated_vram_mb) < _safe_int(resource_caps.get("weight_streaming_headroom_mb"), 6144)
        if estimated_vram_mb >= weight_streaming_trigger or low_headroom:
            use_weight_streaming = True
            notes.append("Weight streaming enabled automatically to protect workstation headroom.")
            estimated_vram_mb, estimated_runtime_seconds = _estimate_plan_metrics(
                width=width,
                height=height,
                frames=frames,
                effective_profile=effective_profile,
                variant_label=variant["label"],
                use_async_offload=use_async_offload,
                use_pinned_memory=use_pinned_memory,
                use_weight_streaming=use_weight_streaming,
                low_workstation_impact=low_workstation_impact,
            )

    memory_warning = _safe_int(resource_caps.get("memory_warning_threshold_mb"), 16384)
    memory_hard = _safe_int(resource_caps.get("memory_hard_threshold_mb"), 20480)
    runtime_warning = _safe_int(resource_caps.get("runtime_warning_threshold_seconds"), 720)
    runtime_hard = _safe_int(resource_caps.get("runtime_hard_threshold_seconds"), 1500)
    downgraded_from: str | None = None

    while effective_profile != "preview" and (
        estimated_vram_mb > memory_hard
        or estimated_runtime_seconds > runtime_hard
        or (low_workstation_impact and effective_profile == "quality")
    ):
        downgraded_from = effective_profile
        effective_profile = "preview" if effective_profile in {"quality", "blender_guided", "first_last_frame"} else "preview"
        workflow_config = workflow_profiles.get(effective_profile, workflow_config)
        width = _safe_int(payload.get("width"), workflow_config.get("width", width))
        height = _safe_int(payload.get("height"), workflow_config.get("height", height))
        frames = _safe_int(payload.get("frames"), workflow_config.get("frames", frames))
        fps = _safe_int(payload.get("fps"), workflow_config.get("fps", fps))
        warnings.append(
            f"Downgraded workflow profile from {downgraded_from} to {effective_profile} because the estimated workload exceeded workstation-safe thresholds."
        )
        estimated_vram_mb, estimated_runtime_seconds = _estimate_plan_metrics(
            width=width,
            height=height,
            frames=frames,
            effective_profile=effective_profile,
            variant_label=variant["label"],
            use_async_offload=use_async_offload,
            use_pinned_memory=use_pinned_memory,
            use_weight_streaming=use_weight_streaming,
            low_workstation_impact=low_workstation_impact,
        )

    if estimated_vram_mb > memory_warning:
        warnings.append("Estimated VRAM use is above the workstation-safe warning threshold.")
    if estimated_runtime_seconds > runtime_warning:
        warnings.append("Estimated runtime is above the workstation-safe warning threshold.")
    if not capabilities.get("capabilities", {}).get("ltx_video_node_available", False):
        warnings.append("LTXVideo nodes were not detected, so LTX-specific workflows will fall back to validation-only guidance.")
    if variant["label"] == "none":
        warnings.append("No suitable local checkpoints were detected for the requested Linux workstation profile.")

    supported = bool(capabilities.get("capabilities", {}).get("nvidia_gpu_present", False)) and variant["label"] != "none"
    validation_ok = supported and (
        capabilities.get("capabilities", {}).get("ltx_video_node_available", False)
        or capabilities.get("checkpoint_inventory", {}).get("ltx_available", False)
    )

    return {
        "machine_role": capabilities.get("role"),
        "active_profile": capabilities.get("active_profile"),
        "requested_profile": requested_profile,
        "effective_profile": effective_profile,
        "downgraded_from": downgraded_from,
        "low_workstation_impact": low_workstation_impact,
        "selected_model_variant": variant,
        "width": width,
        "height": height,
        "frames": frames,
        "fps": fps,
        "estimated_vram_mb": estimated_vram_mb,
        "estimated_runtime_seconds": estimated_runtime_seconds,
        "optimizations": {
            "async_offload": use_async_offload,
            "pinned_memory": use_pinned_memory,
            "weight_streaming": use_weight_streaming,
            "nvfp4": False,
        },
        "thresholds": {
            "memory_warning_threshold_mb": memory_warning,
            "memory_hard_threshold_mb": memory_hard,
            "runtime_warning_threshold_seconds": runtime_warning,
            "runtime_hard_threshold_seconds": runtime_hard,
        },
        "supported": supported,
        "validation_ok": validation_ok,
        "warnings": warnings,
        "notes": notes,
    }


def build_linux_benchmark(
    settings: dict[str, Any],
    capabilities: dict[str, Any],
    measurements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    measurements = measurements or {}
    scenario_requests: list[dict[str, Any]] = []
    checkpoint_inventory = capabilities.get("checkpoint_inventory", {})
    optimizations = capabilities.get("optimizations", {})

    if checkpoint_inventory.get("fp8_available"):
        scenario_requests.append({"name": "preview_fp8_baseline", "profile": "preview", "variant": "fp8", "weight_streaming": False})
    if checkpoint_inventory.get("non_fp8_available"):
        scenario_requests.append({"name": "preview_non_fp8_baseline", "profile": "preview", "variant": "non_fp8", "weight_streaming": False})
    if checkpoint_inventory.get("fp8_available") and optimizations.get("weight_streaming", {}).get("available"):
        scenario_requests.append({"name": "preview_fp8_streaming", "profile": "preview", "variant": "fp8", "weight_streaming": True})
    if checkpoint_inventory.get("non_fp8_available") and optimizations.get("weight_streaming", {}).get("available"):
        scenario_requests.append({"name": "preview_non_fp8_streaming", "profile": "preview", "variant": "non_fp8", "weight_streaming": True})
    if not scenario_requests:
        scenario_requests.append({"name": "preview_fallback", "profile": "preview", "variant": None, "weight_streaming": False})

    scenarios: list[dict[str, Any]] = []
    for item in scenario_requests:
        plan = build_linux_runtime_plan(
            settings,
            capabilities,
            {
                "profile": item["profile"],
                "variant": item.get("variant"),
                "weight_streaming": item.get("weight_streaming"),
                "low_workstation_impact": True,
            },
        )
        stability = 100.0
        interactivity = 100.0
        throughput = 100.0
        thresholds = plan.get("thresholds", {})

        stability -= max(0.0, (plan["estimated_vram_mb"] - thresholds["memory_warning_threshold_mb"]) / 256.0)
        stability -= 12.0 if plan["warnings"] else 0.0
        interactivity -= max(0.0, plan["estimated_runtime_seconds"] / 18.0)
        interactivity -= 18.0 if not plan["low_workstation_impact"] else 0.0
        throughput -= max(0.0, plan["estimated_runtime_seconds"] / 28.0)
        throughput += 6.0 if plan["selected_model_variant"]["label"] == "fp8" else 0.0
        throughput += 3.0 if plan["optimizations"]["async_offload"] else 0.0
        throughput += 2.0 if plan["optimizations"]["weight_streaming"] else 0.0

        scenario = {
            "name": item["name"],
            "plan": plan,
            "stability_score": round(_clamp(stability, 0.0, 100.0), 2),
            "interactivity_score": round(_clamp(interactivity, 0.0, 100.0), 2),
            "throughput_score": round(_clamp(throughput, 0.0, 100.0), 2),
        }
        scenario["overall_score"] = round(
            scenario["stability_score"] * 0.5
            + scenario["interactivity_score"] * 0.3
            + scenario["throughput_score"] * 0.2,
            2,
        )
        scenarios.append(scenario)

    scenarios.sort(key=lambda item: item["overall_score"], reverse=True)
    recommended = scenarios[0] if scenarios else None
    recommended_summary = None
    if recommended is not None:
        plan = recommended["plan"]
        recommended_summary = {
            "scenario": recommended["name"],
            "workflow_profile": plan["effective_profile"],
            "model_variant": plan["selected_model_variant"]["label"],
            "async_offload": plan["optimizations"]["async_offload"],
            "pinned_memory": plan["optimizations"]["pinned_memory"],
            "weight_streaming": plan["optimizations"]["weight_streaming"],
            "low_workstation_impact": plan["low_workstation_impact"],
            "estimated_vram_mb": plan["estimated_vram_mb"],
            "estimated_runtime_seconds": plan["estimated_runtime_seconds"],
        }

    return {
        "machine_role": capabilities.get("role"),
        "active_profile": capabilities.get("active_profile"),
        "captured_at": _now_iso(),
        "measurements": measurements,
        "scenarios": scenarios,
        "recommended_config": recommended_summary,
    }