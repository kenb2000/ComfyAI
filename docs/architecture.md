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

## Flow
1. User writes a prompt.
2. PromptToGraph infers intent and selects a template.
3. The template is parameterized (metadata prompt attached).
4. Output is a JSON graph compatible with ComfyUI.

## Extensibility
- Add more templates to support additional pipelines.
- Swap heuristic mapper with an LLM-based parser.
- Parameterize templates more deeply (e.g., size, steps, CFG) from prompt terms.
