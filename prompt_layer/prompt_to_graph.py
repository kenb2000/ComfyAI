"""
prompt_to_graph.py

A minimal, pure-stdlib prompt-to-graph translator. It maps common
natural-language prompts to graph templates and lightly parameterizes them.

This does NOT remove the manual ComfyUI node editor. It's a thin layer that can
be used to auto-construct a starting graph, which users can then refine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

# Optional LLM assistant
try:
    from .ai_assistant import LlmAssistant  # noqa: F401
except Exception:  # pragma: no cover
    LlmAssistant = None  # type: ignore


class TemplateResolver:
    """Loads JSON graph templates from the templates directory."""

    def __init__(self, templates_dir: Optional[Path] = None) -> None:
        self.templates_dir = (
            templates_dir if templates_dir is not None else Path(__file__).parent / "templates"
        )

    def load(self, name: str) -> Dict[str, Any]:
        path = self.templates_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)


class PromptToGraph:
    """
    Translate a natural-language prompt into a ComfyUI-style graph dict by
    mapping keywords to prebuilt templates and injecting basic metadata.

    Contract:
    - input: prompt (str)
    - output: graph (Dict[str, Any]) with keys: nodes (list), edges (list), metadata (dict)
    - error modes: FileNotFoundError for unknown template file; generic exceptions propagated.
    - success: returns a dict ready to be saved to JSON and loaded into ComfyUI.
    """

    def __init__(self, templates_dir: Optional[Path] = None, assistant: Optional[Any] = None) -> None:
        self.resolver = TemplateResolver(templates_dir)
        self.assistant = assistant

    def infer_intent(self, prompt: str) -> str:
        """Infer which template best matches the prompt.

        This is a simple heuristic keyword matcher; it can be replaced by an LLM-based
        parser later. Prefer explicit matches over generic ones.
        """
        p = prompt.lower()
        p = re.sub(r"\s+", " ", p)

        # First try the optional LLM assistant
        kws: Optional[List[str]] = None
        if getattr(self, "assistant", None) is not None and getattr(self.assistant, "enabled", False):
            try:
                # Assistant is dynamically typed here to avoid import/type issues when not installed
                kws = self.assistant.suggest_intent_keywords(prompt)  # type: ignore[attr-defined]
            except Exception:
                kws = None

        def has_kw(word: str) -> bool:
            if kws:
                return word in kws
            return word in p

        has_sdxl = has_kw("sdxl")
        has_controlnet = has_kw("controlnet")
        has_depth = has_kw("depth")
        has_upscale = any(has_kw(k) for k in ["upscale", "upscaler", "esrgan", "4x"])
        has_refiner = has_kw("refiner")
        has_img2img = any(has_kw(k) for k in ["img2img", "image to image", "image2image"]) 
        has_lora = has_kw("lora")

        if has_controlnet and has_depth and has_upscale:
            return "controlnet_depth_upscale"
        if has_sdxl and (has_refiner or "base" in p):
            return "sdxl_base_refiner"
        if has_img2img and has_lora:
            return "img2img_lora"
        if has_sdxl:
            return "sdxl_base_refiner"
        # fallback
        return "img2img_lora"

    def parameterize(self, graph: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Inject user prompt and light hints into the graph's metadata.

        This keeps templates stable while letting us annotate the run context.
        """
        meta = graph.setdefault("metadata", {})
        meta["source"] = "prompt-layer"
        meta["prompt"] = prompt
        return graph

    def parse(self, prompt: str) -> Dict[str, Any]:
        template_name = self.infer_intent(prompt)
        graph = self.resolver.load(template_name)
        graph = self.parameterize(graph, prompt)
        return graph

    # --- Parameter overrides ---
    def apply_overrides(self, graph: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Apply CLI-style overrides to the simple graph schema.

        Supported overrides:
        - size: 'WxH' string, updates nodes that have width/height inputs (commonly KSampler or downstream image nodes)
        - steps: int, sets steps on KSampler-like nodes
        - cfg: float, sets cfg on KSampler-like nodes
        - seed: int, sets seed on KSampler-like nodes when present
        - input_image: str path for LoadImage-like nodes
        - control_image: str path for control image inputs if present
        """
        nodes = graph.get("nodes", [])

        w = h = None
        size = overrides.get("size")
        if isinstance(size, str) and "x" in size.lower():
            try:
                parts = size.lower().split("x")
                w = int(parts[0].strip())
                h = int(parts[1].strip())
            except Exception:
                w = h = None

        def for_class(name: str) -> list[Dict[str, Any]]:
            return [n for n in nodes if n.get("class_type") == name]

        # Steps / CFG / Seed on KSampler
        for n in for_class("KSampler"):
            inputs = n.setdefault("inputs", {})
            if overrides.get("steps") is not None:
                inputs["steps"] = int(overrides["steps"])  # type: ignore[index]
            if overrides.get("cfg") is not None:
                inputs["cfg"] = float(overrides["cfg"])  # type: ignore[index]
            if overrides.get("seed") is not None:
                inputs["seed"] = int(overrides["seed"])  # type: ignore[index]
        # Steps / CFG / Seed on KSamplerAdvanced (noise_seed)
        for n in for_class("KSamplerAdvanced"):
            inputs = n.setdefault("inputs", {})
            if overrides.get("steps") is not None:
                inputs["steps"] = int(overrides["steps"])  # type: ignore[index]
            if overrides.get("cfg") is not None:
                inputs["cfg"] = float(overrides["cfg"])  # type: ignore[index]
            if overrides.get("seed") is not None:
                inputs["noise_seed"] = int(overrides["seed"])  # type: ignore[index]


        # Size on EmptyLatentImage
        if w and h:
            for n in for_class("EmptyLatentImage"):
                inputs = n.setdefault("inputs", {})
                inputs["width"] = w
                inputs["height"] = h

        # Sampler/scheduler
        sampler = overrides.get("sampler")
        scheduler = overrides.get("scheduler")
        if sampler or scheduler:
            for n in for_class("KSampler") + for_class("KSamplerAdvanced"):
                inputs = n.setdefault("inputs", {})
                if sampler is not None:
                    inputs["sampler_name"] = sampler
                if scheduler is not None:
                    inputs["scheduler"] = scheduler

        # Refiner split: adjust KSamplerAdvanced start/end boundaries when two stages exist
        if isinstance(overrides.get("refiner_split"), int):
            split = int(overrides["refiner_split"])  # type: ignore[index]
            ks = for_class("KSamplerAdvanced")
            if len(ks) >= 2:
                # Heuristic: first is base stage, second is refiner stage
                base, refiner = ks[0], ks[1]
                base_in = base.setdefault("inputs", {})
                ref_in = refiner.setdefault("inputs", {})
                base_in["end_at_step"] = split
                # Ensure refiner starts at split
                ref_in["start_at_step"] = split

        # Input image path
        if overrides.get("input_image"):
            for n in for_class("LoadImage"):
                inputs = n.setdefault("inputs", {})
                inputs["image"] = overrides["input_image"]

        # Control image path (attach to a conventional control image input name if present)
        if overrides.get("control_image"):
            for n in nodes:
                inputs = n.setdefault("inputs", {})
                for key in ("image", "control_image", "guidance_image"):
                    if key in inputs and isinstance(inputs[key], (str, type(None))):
                        inputs[key] = overrides["control_image"]
                        break

        return graph
