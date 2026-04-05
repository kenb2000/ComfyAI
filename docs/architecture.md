# Hybrid ComfyUI: Prompt Layer Architecture

This project adds a prompt-driven automation layer on top of ComfyUI while keeping manual node editing intact.

## Dual Mode
- Prompt Mode: Convert natural language into a starting graph via templates.
- Manual Mode: Open the graph in ComfyUI's editor to tweak nodes and parameters.

## Components
- `prompt_layer/prompt_to_graph.py`: heuristic keyword mapper from prompt to JSON graph.
- `prompt_layer/templates/`: pre-built graph templates for common workflows.
- `prompt_layer/ai_assistant.py`: optional Ollama-powered keyword extraction.
- `prompt_layer/cli.py`: command-line entry point for generating graphs.
- `prompt_layer/planner_client.py`: stdlib HTTP client for the shared planner/helper service.
- `prompt_layer/planner_ui.py`: small browser UI for planner policy, research, and workflow requests.
- `prompt_layer/setup_status.py`: manifest-backed setup inspection for ComfyUI runtime, planner linkage, and optional models.
- `prompt_layer/setup_config.py`: settings loading and path resolution from `settings.json`.
- `prompt_layer/setup_runtime.py`: setup acquisition, sidecar launching, and verification helpers.
- `prompt_layer/setup_status_server.py`: stdlib HTTP endpoint exposing setup routes plus the shared planner bridge UI and proxy routes.

## Runtime Configuration
- `requirements/comfyhybrid_requirements.json` declares the desired runtime contract.
- `settings.json` stores the machine-local resolved paths and ports used by setup, launch, and verify flows.
- By default, setup writes runtime artifacts under repo-local `tools/`.
- Generated planner-authored workflow configs are stored under `tools/workspace/generated-workflows`.

## Shared Planner Bridge
- ComfyUIhybrid does not implement its own planner stack; it talks to the main assistant backend over HTTP.
- `PlannerClient` uses the planner base URL from `settings.json`, defaulting to `http://127.0.0.1:8000`.
- The local HTTP service exposes:
  - `GET /planner/ui`
  - `GET /planner/service/status`
  - `GET /planner/models`
  - `GET` and `POST /planner/policy`
  - `POST /planner/research/run`
  - `POST /planner/service/config`
  - `POST /planner/service/start`
  - `POST /planner/service/stop`
  - `POST /helper/process`
  - `GET /workspace/workflows`
- Planner service health is checked against `GET /health`.
- The configured main assistant repo path is stored in `settings.json` under `planner.assistant_repo_path`.
- Planner sidecar launch uses:
  - Linux: `scripts/run_backend_linux.sh`
  - Windows: `scripts/run_backend_windows.ps1`
- Workflow requests forwarded to `/helper/process` include deterministic local paths such as the repo root, ComfyUI models path, and generated workflows directory.
- The planner remains the authority for planning decisions; ComfyUIhybrid only proxies, visualizes, and stores generated workflow configs locally.

## Flow
1. User writes a prompt.
2. PromptToGraph infers intent and selects a template.
3. The template is parameterized (metadata prompt attached).
4. Output is a JSON graph compatible with ComfyUI.

## Extensibility
- Add more templates to support additional pipelines.
- Swap heuristic mapper with an LLM-based parser.
- Parameterize templates more deeply (e.g., size, steps, CFG) from prompt terms.
