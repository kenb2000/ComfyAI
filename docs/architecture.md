# Hybrid ComfyUI: Prompt Layer Architecture

This project adds a prompt-driven automation layer on top of ComfyUI while keeping manual node editing intact.

## Dual Mode
- Prompt Mode: Convert natural language into a starting graph via templates.
- Manual Mode: Open the graph in ComfyUI's editor to tweak nodes and parameters.

## Components
- `prompt_layer/prompt_to_graph.py`: heuristic keyword mapper from prompt to JSON graph.
- `prompt_layer/templates/`: pre-built graph templates for common workflows.
- `prompt_layer/ai_assistant.py`: optional Ollama-powered keyword extraction.
- `prompt_layer/planner/`: Linux-first local planner config, prompts, validation, and runtime.
- `prompt_layer/cli.py`: command-line entry point for generating graphs.
- `prompt_layer/planner_client.py`: stdlib HTTP client for the shared planner/helper service.
- `prompt_layer/planner_ui.py`: small browser UI for local planner status, verify, rebuild, and workflow requests.
- `prompt_layer/linux_workstation.py`: Linux workstation capability detection, runtime planning, fallback policy, and benchmark scoring.
- `prompt_layer/setup_status.py`: manifest-backed setup inspection for ComfyUI runtime, local planner readiness, and optional models.
- `prompt_layer/setup_config.py`: settings loading and path resolution from `settings.json`.
- `prompt_layer/setup_runtime.py`: setup acquisition, sidecar launching, local planner bootstrap, and verification helpers.
- `prompt_layer/setup_status_server.py`: stdlib HTTP endpoint exposing setup routes plus the local planner UI and helper routes.

## Runtime Configuration
- `requirements/comfyhybrid_requirements.json` declares the desired runtime contract.
- `settings.json` stores the machine-local resolved paths and ports used by setup, launch, and verify flows.
- By default, setup writes runtime artifacts under repo-local `tools/`.
- Generated planner-authored workflow configs are stored under `tools/workspace/generated-workflows`.
- Local planner artifacts are stored under `tools/workspace/planner-output`.
- Linux workstation verification artifacts are stored under `tools/runtime/verification/linux`.
- Linux workstation benchmark artifacts are stored under `tools/runtime/benchmarks/linux`.

## Linux workstation policy
- The repo treats Linux as the stable local development and fallback generation node.
- Active Linux profile: `linux_stable_nvidia`.
- Validated workstation summary: RTX 3090 Ti with `torch 2.6.0+cu124`, `torchvision 0.21.0+cu124`, `torchaudio 2.6.0+cu124`.
- Recommended benchmark scenario: `preview_fp8_baseline`.
- Recommended optimization intent: async offload enabled, pinned memory enabled, weight streaming off for the validated baseline, with automatic weight streaming re-enabled only when VRAM headroom gets tight.
- NVFP4 and other Blackwell-only assumptions are explicitly disabled unless the environment reports support.
- Workflow planning exposes preview, quality, first-frame/last-frame, and Blender-guided modes with conservative workstation-safe resource caps.

## Linux setup and verify behavior
- Linux bootstrap selects a driver-compatible NVIDIA PyTorch CUDA index before installing ComfyUI requirements.
- Linux asset acquisition installs `ComfyUI-LTXVideo` requirements when the node checkout is present or acquired.
- Default Linux acquisition verifies preferred checkpoint targets and no longer assumes that every heavyweight LTX variant should be downloaded.
- Verify treats ComfyUI as healthy only when both `/system_stats` and `/object_info` respond successfully.
- Verify also runs a Falcon-backed local planner smoke test against ComfyUI validation.
- Planner sidecar verification is optional and remains separate from required local planner readiness unless explicitly configured.

## Local Planner
- ComfyAI now implements its own Linux-first local planner stack.
- Falcon 10B 1.58 is the baseline planner/helper model identity because it is the best throughput/concurrency tradeoff for always-on helper use.
- The persisted planner contract in `settings.json` includes:
  - `enabled`
  - `mode = "local"`
  - `default_model_id = "tiiuae/Falcon3-10B-Instruct-1.58bit"`
  - `platform_target = "linux"`
  - Falcon role mapping for dispatcher, planner, and critic
  - `max_repairs_before_fail`
  - `request_timeout_seconds`
  - `planner_output_dir`
  - optional future fields such as `stronger_model_id` and `escalation_enabled`
- The local HTTP service now exposes:
  - `GET /planner/ui`
  - `GET /planner/status`
  - `GET /planner/models`
  - `GET` and `POST /planner/policy`
  - `POST /planner/verify`
  - `POST /planner/rebuild`
  - `POST /helper/process`
  - `GET /workspace/workflows`
- `POST /helper/process` now performs local request dispatch, template selection, parameter filling, validation against ComfyUI `/object_info`, bounded repair, and optional local queue submission.
- Workflow requests still include deterministic local paths plus Linux runtime preferences when the Linux workstation profile is active.
- The main assistant backend remains an optional later escalation path. Optional sidecar routes still exist under `/planner/service/*`, but normal workflow planning no longer depends on them.

## Flow
1. User writes a prompt.
2. PromptToGraph infers intent and selects a template.
3. The template is parameterized (metadata prompt attached).
4. Output is a JSON graph compatible with ComfyUI.

## Extensibility
- Add more templates to support additional pipelines.
- Swap the deterministic Falcon baseline stages with richer on-device generation when the local planner grows beyond v1.
- Parameterize templates more deeply (e.g., size, steps, CFG) from prompt terms.
