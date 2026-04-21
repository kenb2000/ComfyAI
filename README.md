# HybridComfyUI: Prompt-Driven + Manual Precision

Add a prompt-to-graph layer on top of ComfyUI without losing the manual node editor. Start fast with text, refine with nodes.

## What you get
- Prompt Mode: "Generate an SDXL image with ControlNet depth and upscale it" → builds a graph.
- Manual Mode: Load the graph into ComfyUI and fine-tune as usual.
- Built-in Linux-first local planner/helper using Falcon 10B 1.58 for routine workflow preparation.

## Linux workstation role

This repo now treats Linux as the stable local generation, development, and fallback execution target for ComfyUI and LTX workflows.

- Active Linux workstation profile: `linux_stable_nvidia`
- Machine role: stable workstation / development node
- Recommended defaults: FP8 or distilled/dev variants when available, async offload enabled, pinned memory enabled, weight streaming off for the validated baseline
- Explicitly not assumed on Linux: NVFP4 and other Blackwell-only acceleration paths
- Target behavior: stable previews, reliable workflow validation, acceptable local video generation without starving the workstation

The Linux policy prefers LTX-2.3 checkpoints when installed, falls back conservatively when memory/runtime thresholds are crossed, and emits visible warnings instead of silently failing the request path.

Windows Blackwell-class hardware can still be the preferred box for highest-end final renders, but this Linux machine remains a fully capable local-first development and fallback node.

## Linux workstation state summary

Validated Linux workstation state for the repaired setup path:

- GPU validated: NVIDIA GeForce RTX 3090 Ti
- Torch stack validated: `torch 2.6.0+cu124`, `torchvision 0.21.0+cu124`, `torchaudio 2.6.0+cu124`
- Recommended benchmark profile: `preview_fp8_baseline`
- Recommended runtime toggles: async offload on, pinned memory on, weight streaming off

## Linux workstation workflow profiles

- `preview`: default local iteration profile with modest resolution and frame counts
- `quality`: conservative quality mode that preserves workstation responsiveness
- `first_last_frame`: safe defaults for first-frame/last-frame guided work
- `blender_guided`: optional advanced mode enabled only when Blender is present

## Linux bootstrap and verify runbook

Use this path on a Linux workstation when you want the repo to acquire runtime dependencies, install the LTX node, and verify the local ComfyUI instance end to end.

```bash
git submodule update --init --recursive
./scripts/linux_bootstrap_comfyhybrid.sh
./scripts/linux_verify_comfyhybrid.sh
python3 ./scripts/comfyhybrid_setup_flow.py status --json
python3 ./scripts/comfyhybrid_setup_flow.py benchmark --json
```

What Linux bootstrap now does:

- selects a driver-compatible NVIDIA PyTorch CUDA wheel channel before installing ComfyUI requirements
- installs repo requirements, ComfyUI requirements, manager requirements when present, and `ComfyUI-LTXVideo` requirements during Linux asset acquisition
- acquires the configured LTX node checkout and verifies preferred Linux checkpoint targets without downloading every heavyweight LTX variant by default
- writes resolved runtime paths and assigned ports into `settings.json`

What Linux verify now requires before it reports success:

- `GET /system_stats` responds successfully
- `GET /object_info` responds successfully
- ComfyUI is treated as healthy only when both endpoints respond
- the built-in local planner completes a smoke dispatch plus validated plan pass
- the main assistant sidecar remains optional and is not required for normal operation

## Setup (Windows PowerShell)

1) Initialize the pinned ComfyUI submodule:

```powershell
cd .
git submodule update --init --recursive
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
- Launch ComfyUI desktop shell: Tasks: Run Task → "Launch ComfyUI (Tauri Shell)"
 - Generate + Open (heuristic): Tasks: Run Task → "Generate + Open (Prompt Layer)"
 - Generate + Run (heuristic): Tasks: Run Task → "Generate + Run (Prompt Layer)"
 - Generate + Open (LLM): Tasks: Run Task → "Generate + Open (Prompt Layer - LLM)"
 - Generate + Run (LLM): Tasks: Run Task → "Generate + Run (Prompt Layer - LLM)"

## Launch ComfyUI

```powershell
python .\scripts\launch_comfyui.py
```

The launcher prefers the repo venv, enables `ComfyUI-Manager` by default, and uses GPU unless you set `HYBRID_COMFYUI_USE_CPU=1`.

## Launch ComfyUI In A Tauri Shell

The desktop shell lives under `desktop/tauri` and wraps the local ComfyUI server in a native Tauri window.

```powershell
cd .\desktop\tauri
cargo tauri dev
```

What it does:
- opens a native workstation dashboard first
- shows the Linux capability panel and the machine role as the stable workstation / development node
- exposes buttons to launch ComfyUI, run verify, run benchmark, and open the local editor
- reads setup status, verify output, and benchmark results directly from the repo tooling

The shell writes backend startup logs to `tools/runtime/tauri-comfyui-stdout.log` and `tools/runtime/tauri-comfyui-stderr.log`.

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
http://127.0.0.1:8010/setup/benchmark
http://127.0.0.1:8010/ports/status
http://127.0.0.1:8010/health
```

If `8010` is already in use, the setup server now picks the next free port in its deterministic range and logs the final assigned address.

`GET /setup/status` reports:
- whether the local ComfyUI runtime is installed, reachable, and runnable from the configured venv policy
- whether the local planner config, Falcon model path, and local planner runtime are ready
- the Linux workstation role, active profile, capability detection, latest verification artifact, and latest benchmark summary
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
- creating a venv and installing a driver-compatible NVIDIA PyTorch CUDA wheel set on Linux before repo plus ComfyUI requirements
- registering the built-in Falcon planner model from shared storage and optionally downloading it into repo-managed storage when requested
- acquiring optional models into the configured ComfyUI models directory
- verifying or acquiring Linux workstation assets such as the configured LTX node path, `ComfyUI-LTXVideo` requirements, and preferred Linux checkpoint targets
- skipping the bulk download of every heavyweight LTX checkpoint variant during default Linux acquisition
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
- checks that ComfyUI is reachable on `/system_stats`
- verifies that `/object_info` returns successfully
- considers ComfyUI healthy only when both `/system_stats` and `/object_info` respond
- runs a local planner smoke test using the Falcon baseline
- verifies simple template dispatch plus a validated local workflow plan
- records the latest local planner verify timestamp and status in `settings.json`
- leaves planner sidecar checks as an optional separate action
- captures Linux workstation verification artifacts under `tools/runtime/verification/linux`
- evaluates a preview workflow plan plus a heavier fallback workflow plan against workstation-safe thresholds

`POST /setup/benchmark`:
- compares preview-profile Linux plans with FP8 vs non-FP8 where available
- compares offload/pinned-memory baselines with and without weight streaming when supported
- persists results under `tools/runtime/benchmarks/linux`
- returns the recommended Linux workstation-safe config for UI display

The launcher script and setup endpoints now read paths and ports from `settings.json` rather than assuming fixed locations.
They also resolve live assigned ports from the shared registry when a service had to fall back from its preferred default.

Port allocation details, registry path overrides, and stale cleanup notes are documented in `docs/ports.md`.

## Local Planner

ComfyAI now includes an internal Linux-first local planner. Falcon 10B 1.58 is the default planner/helper baseline so routine request classification, template selection, parameter filling, validation, repair, and local queue preparation stay inside this repo.

The main AI assistant remains an optional future escalation path. It is no longer a required dependency for normal workflow planning.

Primary local planner routes served by `prompt_layer.setup_status_server`:

```text
GET  /planner/ui
GET  /planner/status
GET  /planner/service/status
GET  /planner/models
GET  /planner/policy
POST /planner/policy
POST /planner/verify
POST /planner/rebuild
POST /planner/service/config
POST /planner/service/start
POST /planner/service/stop
POST /helper/process
GET  /workspace/workflows
```

Use the planner UI in a browser:

```text
http://127.0.0.1:8010/planner/ui
```

What the local planner does:
- classifies the request into the supported workflow family
- chooses and prepares a repo-local workflow template
- fills deterministic workflow parameters for Linux-first use
- validates the graph against ComfyUI `/object_info`
- runs a bounded repair loop before failing
- optionally queues the validated workflow back to local ComfyUI
- injects the Linux runtime plan so preview, quality, and fallback behavior stays workstation-safe
- saves generated workflow graphs under `tools/workspace/generated-workflows`
- writes planner artifacts under `tools/workspace/planner-output`

Workflow requests sent through the local planner include deterministic local paths such as:
- project root
- settings path
- ComfyUI repo path
- ComfyUI models path
- workspace directory
- generated workflows directory

Persisted local planner settings now live in `settings.json` under `planner` and include:
- `enabled = true`
- `mode = "local"`
- `default_model_id = "tiiuae/Falcon3-10B-Instruct-1.58bit"`
- `platform_target = "linux"`
- `role_mapping.dispatcher_model = "tiiuae/Falcon3-10B-Instruct-1.58bit"`
- `role_mapping.planner_model = "tiiuae/Falcon3-10B-Instruct-1.58bit"`
- `role_mapping.critic_model = "tiiuae/Falcon3-10B-Instruct-1.58bit"`
- `max_repairs_before_fail = 2`
- `request_timeout_seconds`
- `planner_output_dir`
- optional future fields such as `stronger_model_id` and `escalation_enabled = false`

Optional assistant sidecar launch details remain available for later escalation:
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
python3 ./scripts/comfyhybrid_setup_flow.py status --json
python3 ./scripts/comfyhybrid_setup_flow.py benchmark --json
```

The bootstrap wrappers call `GET /setup/status`, `POST /setup/acquire`, and `POST /setup/verify` in order, then print a clear PASS/FAIL summary. The verify wrappers call status plus verify only. Both wrappers read the repo's `settings.json` through the setup service and do not require hand-editing runtime paths.

### Linux troubleshooting

- Empty placeholder `comfyui/` blocks clone or submodule init:
  If the directory exists but is not a real checkout, remove the placeholder contents and rerun `git submodule update --init --recursive` or the Linux bootstrap wrapper.
- Copied tree has `.gitmodules` but no live Git checkout:
  If the repo was copied without `.git/`, submodule update will not work. Re-clone the repo as a real Git checkout before running bootstrap.
- Ephemeral port `0` handling:
  `scripts/comfyhybrid_setup_flow.py --server-port 0` requests an ephemeral bind for the local setup server. The assigned port is printed at launch and exposed through `/ports/status`.
- ComfyUI fails during `torch.cuda` init:
  If startup logs mention that the NVIDIA driver is too old or CUDA initialization failed, the installed PyTorch CUDA wheel outruns the workstation driver. Reinstall the driver-compatible channel selected by Linux bootstrap or update the NVIDIA driver first.
- Planner model missing:
  If `/planner/status` shows `missing_model`, register the Falcon model with `POST /planner/rebuild` or rerun setup acquire after pointing `planner.shared_storage_candidates` or `planner.model_path` at the local Falcon checkout.
- Planner validation fails:
  If `/planner/verify` fails, confirm that ComfyUI `/object_info` is reachable and that the required node families for the selected template are present. The local planner only reports ready after the bounded repair loop either succeeds or cleanly fails.

### Next optional upgrades

- Acquire BF16 and other dev checkpoint variants once the baseline FP8 workstation path is stable.
- Integrate optional planner sidecar verification into the regular Linux verify pass when the assistant backend is present.

Optional Windows assistant-sidecar smoke test:

```powershell
.\scripts\smoke_comfyhybrid_planner_windows.ps1
```

It uses the fixed planner backend default `http://127.0.0.1:8000` and checks:
- `GET /health`
- the optional assistant-sidecar routes that remain available for later escalation

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
- `PromptToGraph` and the local planner classify the request, choose a template, and attach your prompt in metadata.
- `prompt_layer/planner/` validates the resulting workflow against ComfyUI, applies a bounded repair loop, and prepares local queue submission.
- You can swap in `ai_assistant.py` to use a local LLM for smarter intent extraction later.

## Next steps
- Add more templates and parameter extraction (size, steps, CFG, LoRA strength).
- GUI toggle between Prompt Mode and Manual Mode inside ComfyUI (future work).
- Deeper integration with ComfyUI's execution engine for one-click runs.
