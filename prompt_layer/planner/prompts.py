"""Prompt contracts for the local planner stages.

The runtime is deliberately deterministic in v1, but these prompt contracts are
kept alongside the planner so ComfyAI has a stable local planning interface as
future on-device generation is added.
"""

from __future__ import annotations

from typing import Any


DISPATCH_PROMPT = (
    "Classify the request into one of the supported workflow families: "
    "controlnet_depth_upscale, sdxl_base_refiner, or img2img_lora."
)

PLAN_PROMPT = (
    "Produce deterministic workflow parameters for the selected template while "
    "keeping Linux workstation overhead low and queueing-ready."
)

CRITIC_PROMPT = (
    "Validate the workflow against ComfyUI /object_info and repair it with a "
    "bounded fallback loop before the request is queued."
)


def planner_prompt_bundle() -> dict[str, Any]:
    return {
        "dispatch": DISPATCH_PROMPT,
        "plan": PLAN_PROMPT,
        "critic": CRITIC_PROMPT,
    }
