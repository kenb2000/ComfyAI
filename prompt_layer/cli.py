"""CLI for the prompt layer.

Example:
  python -m prompt_layer.cli "Generate an SDXL image with ControlNet depth and upscale it" --out ./graph.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .prompt_to_graph import PromptToGraph


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prompt-to-graph translator for ComfyUI")
    p.add_argument("prompt", type=str, help="Natural language instruction")
    p.add_argument("--out", type=Path, default=None, help="Path to save the generated graph JSON")
    p.add_argument("--templates", type=Path, default=None, help="Custom templates directory")
    p.add_argument("--llm", action="store_true", help="Use local LLM (Ollama) to help infer intent keywords")
    p.add_argument("--llm-model", type=str, default="llama3.1", help="Ollama model name to use when --llm is set")
    # Parameter overrides
    p.add_argument("--size", type=str, default=None, help="Image size WxH, e.g., 1024x1024")
    p.add_argument("--steps", type=int, default=None, help="Sampler steps")
    p.add_argument("--cfg", type=float, default=None, help="CFG scale")
    p.add_argument("--seed", type=int, default=None, help="Generation seed")
    p.add_argument("--sampler", type=str, default=None, help="Sampler name (e.g., euler, dpmpp_2m, ddim)")
    p.add_argument("--scheduler", type=str, default=None, help="Scheduler name (e.g., normal, karras, simple)")
    p.add_argument("--refiner-split", type=int, default=None, help="For two-stage SDXL, step index where refiner takes over (sets end_at_step/start_at_step)")
    p.add_argument("--input-image", type=Path, default=None, help="Path to an input image (img2img)")
    p.add_argument("--control-image", type=Path, default=None, help="Path to a ControlNet guidance image")
    # Output format
    p.add_argument("--format", type=str, choices=["internal", "comfy-prompt"], default="internal", help="Output format: internal schema or ComfyUI prompt schema")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assistant = None
    if args.llm:
        try:
            from .ai_assistant import LlmAssistant
            assistant = LlmAssistant(model=args.llm_model, enabled=True)
        except Exception:
            assistant = None

    translator = PromptToGraph(templates_dir=args.templates, assistant=assistant)
    graph = translator.parse(args.prompt)

    # Parameter overrides
    overrides = {
        "size": args.size,
        "steps": args.steps,
        "cfg": args.cfg,
        "seed": args.seed,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "refiner_split": args.refiner_split,
        "input_image": str(args.input_image) if args.input_image else None,
        "control_image": str(args.control_image) if args.control_image else None,
    }
    graph = translator.apply_overrides(graph, overrides)

    if args.format == "comfy-prompt":
        try:
            from .graph_schema import to_comfy_prompt
            graph = to_comfy_prompt(graph)
        except Exception as e:
            raise SystemExit(f"Failed to convert to ComfyUI prompt format: {e}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2)
    else:
        print(json.dumps(graph, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
