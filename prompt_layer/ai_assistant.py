"""
ai_assistant.py

Optional LLM-assisted intent extraction. Uses Ollama if available; otherwise it
silently disables itself. This module is not required for the core functionality
and is safe to ignore if no local LLM runtime is installed.
"""
from __future__ import annotations

from typing import List, Optional

try:
    import json
    import re
    import ollama  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ollama = None  # type: ignore


class LlmAssistant:
    """Thin wrapper around a local LLM to extract intent keywords.

    If Ollama is not installed or fails, methods return None gracefully.
    """

    def __init__(self, model: str = "llama3.1", enabled: bool = True) -> None:
        self.model = model
        self.enabled = enabled and (ollama is not None)

    def suggest_intent_keywords(self, prompt: str) -> Optional[List[str]]:
        if not self.enabled:
            return None
        try:
            instruction = (
                "Extract which of these keywords apply: sdxl, controlnet, depth, "
                "upscale, lora, img2img, refiner. Respond ONLY with a JSON object "
                "like: {\"keywords\": [\"sdxl\", \"controlnet\", ...]} for: "
                f"{prompt}"
            )
            res = ollama.chat(  # type: ignore[union-attr]
                model=self.model,
                messages=[{"role": "user", "content": instruction}],
            )
            content = res.get("message", {}).get("content", "{}")
            # pick first JSON-looking block
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                data = json.loads(m.group(0))
                kws = data.get("keywords")
                if isinstance(kws, list):
                    return [str(k).lower() for k in kws]
        except Exception:
            return None
        return None
