# HybridComfyUI: Prompt-Driven + Manual Precision

Add a prompt-to-graph layer on top of ComfyUI without losing the manual node editor. Start fast with text, refine with nodes.

## What you get
- Prompt Mode: "Generate an SDXL image with ControlNet depth and upscale it" → builds a graph.
- Manual Mode: Load the graph into ComfyUI and fine-tune as usual.
- Optional local LLM (Ollama) to help parse prompts.

## Setup (Windows PowerShell)

1) Clone ComfyUI into `comfyui/`:

```powershell
cd .
 git clone https://github.com/comfyanonymous/ComfyUI.git .\comfyui
```

2) (Optional) Create a virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

3) (Optional) Install extras for local LLM assistance (Ollama):

```powershell
# Only if you plan to use the optional AI assistant
# 1) Install the Ollama runtime for Windows from https://ollama.com
# 2) Start the Ollama service (via the Ollama app)
# 3) Install the Python client in your venv:
python -m pip install ollama
```

## Generate a graph from a prompt

```powershell
# Heuristic mode (no LLM):
python -m prompt_layer.cli "Generate an SDXL image with ControlNet depth and upscale it" --out .\tests\out_graph.json

# LLM-assisted mode (requires Ollama runtime + Python package):
python -m prompt_layer.cli "Generate an SDXL image with ControlNet depth and upscale it" --llm --llm-model llama3.1 --out .\tests\out_graph.json

# Override sampler/scheduler or refiner split:
python -m prompt_layer.cli "Create SDXL base with refiner" --sampler ddim --scheduler karras --refiner-split 12 --steps 28 --cfg 6.5 --out .\tests\out_graph.json
```

Validate structure:

```powershell
python .\scripts\validate_graph.py .\tests\out_graph.json
```

## Run tests (stdlib unittest)

```powershell
python -m unittest -q
```

## VS Code tasks

- Generate graph: Tasks: Run Task → "Generate Graph (Prompt Layer)"
- Generate graph (LLM): Tasks: Run Task → "Generate Graph (Prompt Layer - LLM)"
- Generate + validate (LLM): Tasks: Run Task → "Generate + Validate (Prompt Layer - LLM)"
- Validate graph JSON: Tasks: Run Task → "Validate Graph JSON"
- Validate graph JSON (LLM): Tasks: Run Task → "Validate Graph JSON (LLM)"
- Launch ComfyUI server: Tasks: Run Task → "Launch ComfyUI"
 - Generate + Open (heuristic): Tasks: Run Task → "Generate + Open (Prompt Layer)"
 - Generate + Run (heuristic): Tasks: Run Task → "Generate + Run (Prompt Layer)"
 - Generate + Open (LLM): Tasks: Run Task → "Generate + Open (Prompt Layer - LLM)"
 - Generate + Run (LLM): Tasks: Run Task → "Generate + Run (Prompt Layer - LLM)"

## Launch ComfyUI

```powershell
python .\scripts\launch_comfyui.py
```

The launcher prefers the repo venv, enables `ComfyUI-Manager` by default, and uses GPU unless you set `HYBRID_COMFYUI_USE_CPU=1`.

### Open or run in ComfyUI via API

With ComfyUI running locally (defaults to http://127.0.0.1:8188), you can open or run a generated workflow:

```powershell
# Open in the editor (posts to /prompt)
python .\scripts\open_in_comfyui.py .\tests\out_graph.json

# Submit and poll until outputs are available
python .\scripts\run_workflow.py .\tests\out_graph.json --poll
```

Tasks:
- Open in ComfyUI: Tasks: Run Task → "Open in ComfyUI"
- Run workflow (poll): Tasks: Run Task → "Run Workflow (poll)"

If launching ComfyUI fails, ensure you’ve installed ComfyUI’s own dependencies inside your environment.

## How it works
- Templates in `prompt_layer/templates/` define common workflows (SDXL base+refiner, img2img+LoRA, ControlNet depth+upscale).
- `PromptToGraph` picks a template via simple keyword heuristics and attaches your prompt in metadata.
- You can swap in `ai_assistant.py` to use a local LLM for smarter intent extraction later.

## Next steps
- Add more templates and parameter extraction (size, steps, CFG, LoRA strength).
- GUI toggle between Prompt Mode and Manual Mode inside ComfyUI (future work).
- Deeper integration with ComfyUI's execution engine for one-click runs.
