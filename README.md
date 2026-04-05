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

## Setup Status Endpoint

The repo now includes:
- a manifest at `requirements/comfyhybrid_requirements.json`
- a machine-local `settings.json` written by setup flows
- a small HTTP setup service exposing status, acquire, and verify endpoints
- a shared cross-repo master port registry with deterministic fallback allocation

```powershell
python -m prompt_layer.setup_status_server
```

Default endpoints:

```text
http://127.0.0.1:8010/planner/ui
http://127.0.0.1:8010/setup/status
http://127.0.0.1:8010/setup/acquire
http://127.0.0.1:8010/setup/verify
http://127.0.0.1:8010/ports/status
http://127.0.0.1:8010/health
```

If `8010` is already in use, the setup server now picks the next free port in its deterministic range and logs the final assigned address.

`GET /setup/status` reports:
- whether the local ComfyUI runtime is installed, reachable, and runnable from the configured venv policy
- whether the planner endpoint is reachable
- which optional Comfy model files from the manifest are present or missing under `comfyui/models`
- the current repo port assignments resolved from the shared master registry

`GET /ports/status` reports:
- current assigned ports for this repo
- stale reservations found during cleanup
- detected registry conflicts for this repo

`GET /health` reports:
- `ok=true`
- the repo `app_id`
- the same resolved assigned ports block returned by `/ports/status`

`POST /setup/acquire` streams NDJSON progress and supports:
- acquiring or updating the ComfyUI checkout
- creating a venv and installing repo plus ComfyUI requirements
- acquiring optional models into the configured ComfyUI models directory
- writing `settings.json` with the configured bind/port and tool paths

Example:

```powershell
$body = @{
  acquire_comfyui = $true
  create_venv = $true
  acquire_optional_models = $false
  configure_ports = $true
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri http://127.0.0.1:8010/setup/acquire `
  -Method POST `
  -ContentType application/json `
  -Body $body
```

`POST /setup/verify`:
- checks that ComfyUI is reachable on the configured health endpoint
- verifies that `/object_info` returns successfully
- optionally checks the planner base URL
- launches ComfyUI or a configured planner sidecar if needed

The launcher script and setup endpoints now read paths and ports from `settings.json` rather than assuming fixed locations.
They also resolve live assigned ports from the shared registry when a service had to fall back from its preferred default.

Port allocation details, registry path overrides, and stale cleanup notes are documented in `docs/ports.md`.

## Shared Planner Bridge

ComfyUIhybrid can now use the main assistant's planner/helper service instead of maintaining a second planner stack locally.

Local bridge routes served by `prompt_layer.setup_status_server`:

```text
GET  /planner/ui
GET  /planner/service/status
GET  /planner/models
GET  /planner/policy
POST /planner/policy
POST /planner/research/run
POST /planner/service/config
POST /planner/service/start
POST /planner/service/stop
POST /helper/process
GET  /workspace/workflows
```

The bridge reads the planner base URL from `settings.json` and defaults to `http://127.0.0.1:8000`.

Use the planner UI in a browser:

```text
http://127.0.0.1:8010/planner/ui
```

What the bridge does:
- checks planner health at `/health`
- lets you save the main assistant repo path once in `settings.json`
- starts or stops the planner/helper backend as a local sidecar on Linux or Windows
- fetches planner policy and available manual models from the shared assistant backend
- exposes Manual, Auto, and Research planner modes in the local UI
- forwards workflow requests to the shared `/helper/process` pipeline and streams NDJSON events back to the browser
- saves generated workflow configs under the repo-local workspace area at `tools/workspace/generated-workflows`

Workflow requests sent through the bridge include deterministic local paths such as:
- project root
- settings path
- ComfyUI repo path
- ComfyUI models path
- workspace directory
- generated workflows directory

Planner service launch details:
- health check: `GET http://127.0.0.1:8000/health`
- repo path setting: `planner.assistant_repo_path` in `settings.json`
- Linux launch script: `scripts/run_backend_linux.sh` from the configured assistant repo
- Windows launch script: `scripts/run_backend_windows.ps1` from the configured assistant repo

Repo-root bootstrap wrappers are also included:

```powershell
.\scripts\windows_bootstrap_comfyhybrid.ps1
.\scripts\windows_verify_comfyhybrid.ps1
```

```bash
./scripts/linux_bootstrap_comfyhybrid.sh
./scripts/linux_verify_comfyhybrid.sh
```

The bootstrap wrappers call `GET /setup/status`, `POST /setup/acquire`, and `POST /setup/verify` in order, then print a clear PASS/FAIL summary. The verify wrappers call status plus verify only. Both wrappers read the repo's `settings.json` through the setup service and do not require hand-editing runtime paths.

Windows planner smoke test against the shared assistant backend:

```powershell
.\scripts\smoke_comfyhybrid_planner_windows.ps1
```

It uses the fixed planner backend default `http://127.0.0.1:8000` and checks:
- `GET /health`
- `GET /planner/models`
- `GET /planner/policy`
- `POST /planner/policy`
- `POST /planner/research/run`
- `POST /helper/process`

### Port status CLI

Print the repo's resolved port assignments without starting the server:

```powershell
comfyhybrid-ports
```

or:

```powershell
python -m prompt_layer.setup_status_server ports
```

### Open or run in ComfyUI via API

With ComfyUI running locally, the helper scripts now default to the bind address and port from `settings.json` and only fall back to `http://127.0.0.1:8188` if no settings file is present:

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
