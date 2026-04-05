import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from prompt_layer.setup_config import default_settings, deep_merge, save_settings
from prompt_layer.setup_status_server import make_setup_status_handler


class _FakePlannerHandler(BaseHTTPRequestHandler):
    auto_best_ladder = {
        "saved_at": "2026-04-04T19:30:00Z",
        "baseline_model": "falcon-default",
        "tier_mappings": [
            {"tier": "fast", "model": "falcon-fast", "threshold": "prompt_tokens <= 2000"},
            {"tier": "default", "model": "falcon-default", "threshold": "prompt_tokens <= 12000"},
            {"tier": "research", "model": "falcon-research", "threshold": "prompt_tokens > 12000"},
        ],
        "thresholds": {
            "prompt_tokens_fast_max": 2000,
            "prompt_tokens_default_max": 12000,
        },
    }
    policy = {
        "mode": "auto",
        "manual": {"model": "falcon-default"},
        "research": {"passes": 2, "timeout_seconds": 90, "fallback_model": "falcon-fast"},
        "auto_best_ladder": auto_best_ladder,
    }
    models = ["falcon-default", "falcon-fast", "falcon-research"]
    last_research_payload = None
    last_helper_payload = None

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._write_json({"ok": True})
            return
        if self.path == "/planner/policy":
            self._write_json(type(self).policy)
            return
        if self.path == "/planner/models":
            self._write_json({"models": type(self).models})
            return
        self._write_json({"error": "not_found"}, status=404)

    def do_POST(self):  # noqa: N802
        if self.path == "/planner/policy":
            type(self).policy = self._read_json()
            self._write_json(type(self).policy)
            return
        if self.path == "/planner/research/run":
            type(self).last_research_payload = self._read_json()
            type(self).policy = {
                **type(self).policy,
                "mode": "auto",
                "auto_best_ladder": type(self).auto_best_ladder,
            }
            self._write_json({"status": "completed", "received": type(self).last_research_payload})
            return
        if self.path == "/helper/process":
            type(self).last_helper_payload = self._read_json()
            lines = [
                {"event": "tool_call", "tool": "deterministic_paths"},
                {"event": "tool_result", "tool": "deterministic_paths", "result": {"ok": True}},
                {
                    "event": "result",
                    "workflow": {
                        "nodes": [],
                        "edges": [],
                        "metadata": {"source": "planner", "prompt": type(self).last_helper_payload.get("prompt", "")},
                    },
                },
            ]
            body = "".join(json.dumps(line) + "\n" for line in lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._write_json({"error": "not_found"}, status=404)

    def log_message(self, format, *args):  # noqa: A003
        return


class TestPlannerBridge(unittest.TestCase):
    def _start_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _stop_server(self, server, thread):
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def _reserve_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _write_manifest(self, root: Path, planner_url: str) -> Path:
        manifest_dir = root / "requirements"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "comfyhybrid_requirements.json"
        manifest = {
            "manifest_version": 1,
            "comfyui_runtime": {
                "comfyui_repo_source": "",
                "local_repo_relative_path": "comfyui",
                "python_version_constraints": ">=3.10,<3.13",
                "comfyui_port": 8188,
                "bind_address": "127.0.0.1",
                "venv_path_policy": {"mode": "tool_folder"},
            },
            "planner_service": {
                "planner_base_url": planner_url,
                "health_endpoint": "/health",
                "can_launch_planner_as_sidecar": True,
                "assistant_repo_path": "",
                "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
            },
            "optional_comfy_models": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path

    def test_planner_policy_models_research_and_ui_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            planner_server, planner_thread = self._start_server(_FakePlannerHandler)
            self.addCleanup(self._stop_server, planner_server, planner_thread)

            manifest_path = self._write_manifest(root, f"http://127.0.0.1:{planner_server.server_port}")
            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {"base_url": f"http://127.0.0.1:{planner_server.server_port}"},
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/ui", timeout=5) as response:
                html = response.read().decode("utf-8")
            self.assertIn("Model mode selector", html)
            self.assertIn("/planner/models", html)
            self.assertIn("Run Research", html)
            self.assertIn("Start Planner Service", html)
            self.assertIn("Main assistant repo path", html)
            self.assertIn("No cached best ladder is available yet.", html)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/models", timeout=5) as response:
                models = json.loads(response.read().decode("utf-8"))
            self.assertEqual(models["models"][0], "falcon-default")

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/policy", timeout=5) as response:
                policy = json.loads(response.read().decode("utf-8"))
            self.assertEqual(policy["mode"], "auto")
            self.assertTrue(policy["auto_best_ladder_cache"]["available"])

            saved_settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
            ladder_cache = saved_settings["planner"]["auto_best_ladder_cache"]
            self.assertTrue(ladder_cache["available"])
            self.assertIn("falcon-default", json.dumps(ladder_cache["summary"]["baseline"]))
            self.assertTrue(ladder_cache["summary"]["tier_mappings"])
            self.assertTrue(ladder_cache["summary"]["thresholds"])

            new_policy = {
                "mode": "research",
                "manual": {"model": "falcon-fast"},
                "research": {"passes": 3, "timeout_seconds": 120, "fallback_model": "falcon-default"},
            }
            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/policy",
                data=json.dumps(new_policy).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=5) as response:
                updated = json.loads(response.read().decode("utf-8"))
            self.assertEqual(updated["research"]["passes"], 3)

            research_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/research/run",
                data=json.dumps(new_policy).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(research_req, timeout=5) as response:
                research = json.loads(response.read().decode("utf-8"))
            self.assertEqual(research["status"], "completed")
            self.assertEqual(_FakePlannerHandler.last_research_payload["mode"], "research")
            self.assertTrue(research["auto_best_ladder_cache"]["available"])
            self.assertEqual(research["policy_after"]["mode"], "auto")

    def test_planner_service_config_start_and_stop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            planner_port = self._reserve_free_port()
            assistant_repo = root / "assistant-repo"
            scripts_dir = assistant_repo / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            (scripts_dir / "fake_backend.py").write_text(
                (
                    "from __future__ import annotations\n"
                    "import json, os\n"
                    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
                    "from urllib.parse import urlsplit\n"
                    "base_url = os.environ.get('PLANNER_BASE_URL', 'http://127.0.0.1:8000')\n"
                    "parsed = urlsplit(base_url)\n"
                    "host = parsed.hostname or '127.0.0.1'\n"
                    "port = parsed.port or 8000\n"
                    "class H(BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n"
                    "        body = json.dumps({'ok': True}).encode('utf-8') if self.path == '/health' else b'{}'\n"
                    "        self.send_response(200 if self.path == '/health' else 404)\n"
                    "        self.send_header('Content-Type', 'application/json')\n"
                    "        self.send_header('Content-Length', str(len(body)))\n"
                    "        self.end_headers()\n"
                    "        self.wfile.write(body)\n"
                    "    def log_message(self, format, *args):\n"
                    "        return\n"
                    "server = ThreadingHTTPServer((host, port), H)\n"
                    "server.serve_forever()\n"
                ),
                encoding="utf-8",
            )
            (scripts_dir / "run_backend_windows.ps1").write_text(
                (
                    "$python = $env:PYTHON_EXECUTABLE\n"
                    "if (-not $python) { $python = 'python' }\n"
                    "& $python (Join-Path $PSScriptRoot 'fake_backend.py')\n"
                ),
                encoding="utf-8",
            )
            (scripts_dir / "run_backend_linux.sh").write_text(
                (
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "PYTHON_BIN=\"${PYTHON_EXECUTABLE:-python3}\"\n"
                    "exec \"$PYTHON_BIN\" \"$PWD/scripts/fake_backend.py\"\n"
                ),
                encoding="utf-8",
            )

            manifest_path = self._write_manifest(root, f"http://127.0.0.1:{planner_port}")
            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {
                        "base_url": f"http://127.0.0.1:{planner_port}",
                    },
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/service/status", timeout=5) as response:
                status_before = json.loads(response.read().decode("utf-8"))
            self.assertFalse(status_before["healthy"])
            self.assertFalse(status_before["can_start"])

            config_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/service/config",
                data=json.dumps({"assistant_repo_path": "assistant-repo"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(config_req, timeout=5) as response:
                configured = json.loads(response.read().decode("utf-8"))
            self.assertTrue(configured["assistant_repo_exists"])
            self.assertTrue(configured["can_start"])

            saved_settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["planner"]["assistant_repo_path"], "assistant-repo")
            self.assertIn("run_backend_linux.sh", json.dumps(saved_settings["planner"]["sidecar_launch"]["linux_command"]))
            self.assertIn("run_backend_windows.ps1", json.dumps(saved_settings["planner"]["sidecar_launch"]["windows_command"]))

            start_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/service/start",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(start_req, timeout=30) as response:
                started = json.loads(response.read().decode("utf-8"))
            self.assertTrue(started["ok"])
            self.assertTrue(started["status"]["healthy"])
            self.assertTrue(started["status"]["can_stop"])

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/service/status", timeout=5) as response:
                status_running = json.loads(response.read().decode("utf-8"))
            self.assertTrue(status_running["healthy"])
            self.assertTrue(status_running["pid_running"])

            stop_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/service/stop",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(stop_req, timeout=30) as response:
                stopped = json.loads(response.read().decode("utf-8"))
            self.assertTrue(stopped["ok"])
            self.assertFalse(stopped["status"]["healthy"])

    def test_helper_process_streams_events_and_saves_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            planner_server, planner_thread = self._start_server(_FakePlannerHandler)
            self.addCleanup(self._stop_server, planner_server, planner_thread)

            manifest_path = self._write_manifest(root, f"http://127.0.0.1:{planner_server.server_port}")
            (root / "comfyui" / "models").mkdir(parents=True, exist_ok=True)
            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {"base_url": f"http://127.0.0.1:{planner_server.server_port}"},
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            payload = {
                "prompt": "make a depth upscale workflow",
                "mode": "auto",
            }
            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/helper/process",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=15) as response:
                lines = [json.loads(line) for line in response.read().decode("utf-8").splitlines() if line.strip()]

            event_names = [line["event"] for line in lines]
            self.assertIn("tool_call", event_names)
            self.assertIn("tool_result", event_names)
            self.assertIn("workflow_saved", event_names)

            helper_payload = _FakePlannerHandler.last_helper_payload
            self.assertIn("deterministic_paths", helper_payload)
            self.assertIn("generated_workflows_dir", helper_payload["deterministic_paths"])
            self.assertIn("comfyui_models_path", helper_payload["deterministic_paths"])

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/workspace/workflows", timeout=5) as response:
                workflows = json.loads(response.read().decode("utf-8"))
            self.assertEqual(workflows["count"], 1)
            saved_file = root / workflows["items"][0]["relative_path"]
            self.assertTrue(saved_file.exists())


if __name__ == "__main__":
    unittest.main()
