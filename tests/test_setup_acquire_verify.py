import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from prompt_layer.setup_config import default_settings, deep_merge, save_settings
from prompt_layer.setup_runtime import _select_linux_nvidia_torch_channel
from prompt_layer.setup_status_server import make_setup_status_handler


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


class _PlannerCapableComfyHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/system_stats", "/"):
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
        elif self.path == "/object_info":
            body = json.dumps(
                {
                    "CheckpointLoaderSimple": {},
                    "CLIPTextEncode": {},
                    "EmptyLatentImage": {},
                    "KSamplerAdvanced": {},
                    "VAEDecode": {},
                    "SaveImage": {},
                    "LoadImage": {},
                    "LoraLoader": {},
                    "VAEEncode": {},
                    "KSampler": {},
                    "ControlNetLoader": {},
                    "ControlNetApplyAdvanced": {},
                    "LatentUpscaleBy": {},
                    "AsyncOffload": {},
                    "PinnedMemory": {},
                    "WeightStreaming": {},
                    "LTXVideoSampler": {},
                }
            ).encode("utf-8")
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


class _ComfyBenchmarkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/system_stats":
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
        elif self.path == "/object_info":
            body = json.dumps({"LTXVideoSampler": {}, "AsyncOffload": {}, "PinnedMemory": {}, "WeightStreaming": {}}).encode("utf-8")
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


class _NotPlannerHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"{}"
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


class TestSetupAcquireVerify(unittest.TestCase):
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

    def _kill_pid(self, pid: int) -> None:
        if not pid:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        time.sleep(0.2)

    def _write_manifest(self, root: Path, content: dict) -> Path:
        manifest_dir = root / "requirements"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "comfyhybrid_requirements.json"
        manifest_path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        return manifest_path

    def _write_fake_planner_model(self, root: Path) -> Path:
        model_dir = root / "shared-models" / "Falcon3-10B-Instruct-1.58bit"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model.safetensors").write_bytes(b"12345678")
        return model_dir

    def test_select_linux_nvidia_torch_channel_prefers_driver_compatible_index(self):
        self.assertEqual(_select_linux_nvidia_torch_channel("550.163.01")["channel"], "cu124")
        self.assertEqual(_select_linux_nvidia_torch_channel("535.216.03")["channel"], "cu121")
        self.assertEqual(_select_linux_nvidia_torch_channel("580.12.01")["channel"], "cu130")

    def _init_git_repo(self, repo_path: Path) -> None:
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True, text=True)

    def test_setup_acquire_endpoint_clones_writes_settings_and_copies_models(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_repo = root / "source-comfyui"
            source_repo.mkdir(parents=True, exist_ok=True)
            (source_repo / "main.py").write_text("print('fake comfy')\n", encoding="utf-8")
            (source_repo / "requirements.txt").write_text("", encoding="utf-8")
            (source_repo / "manager_requirements.txt").write_text("", encoding="utf-8")
            self._init_git_repo(source_repo)

            local_model = root / "downloads" / "sdxl_base.safetensors"
            local_model.parent.mkdir(parents=True, exist_ok=True)
            local_model.write_bytes(b"12345678")
            planner_model = self._write_fake_planner_model(root)

            manifest_path = self._write_manifest(
                root,
                {
                    "manifest_version": 1,
                    "comfyui_runtime": {
                        "comfyui_repo_source": str(source_repo.resolve()),
                        "local_repo_relative_path": "runtime/comfyui",
                        "python_version_constraints": ">=3.10,<3.13",
                        "comfyui_port": 8188,
                        "bind_address": "127.0.0.1",
                        "venv_path_policy": {"mode": "tool_folder"},
                    },
                    "planner_service": {
                        "planner_base_url": "http://127.0.0.1:8000",
                        "can_launch_planner_as_sidecar": False,
                        "assistant_repo_path": "",
                        "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                    },
                    "local_planner": {
                        "shared_storage_candidates": [str(planner_model)],
                    },
                    "optional_comfy_models": [
                        {
                            "model_name": "sdxl_base.safetensors",
                            "type": "checkpoint",
                            "source": "local_path",
                            "source_value": "downloads/sdxl_base.safetensors",
                            "expected_size_min_bytes": 8,
                            "sha256": None,
                            "target_path_relative_to_models": "checkpoints/sdxl_base.safetensors",
                        }
                    ],
                },
            )

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            body = json.dumps(
                {
                    "configure_ports": True,
                    "acquire_comfyui": True,
                    "create_venv": False,
                    "acquire_optional_models": True,
                    "comfyui": {"bind_address": "127.0.0.1", "port": 9191},
                }
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/setup/acquire",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=30) as response:
                lines = [json.loads(line) for line in response.read().decode("utf-8").splitlines() if line.strip()]

            self.assertTrue(any(line.get("event") == "complete" and line.get("ok") is True for line in lines))
            self.assertTrue(any(line.get("step") == "acquire_linux_workstation_assets" for line in lines))
            self.assertTrue(any(line.get("step") == "acquire_local_planner" for line in lines))
            self.assertTrue((root / "settings.json").exists())
            self.assertTrue((root / "runtime" / "comfyui" / "main.py").exists())
            self.assertTrue((root / "runtime" / "comfyui" / "models" / "checkpoints" / "sdxl_base.safetensors").exists())

            saved_settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["comfyui"]["port"], 9191)
            self.assertEqual(saved_settings["planner"]["model_path"], str(planner_model.resolve()))

    def test_setup_verify_endpoint_launches_configured_comfyui(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comfy_port = self._reserve_free_port()
            planner_model = self._write_fake_planner_model(root)
            manifest_path = self._write_manifest(
                root,
                {
                    "manifest_version": 1,
                    "comfyui_runtime": {
                        "comfyui_repo_source": "",
                        "local_repo_relative_path": "runtime/comfyui",
                        "python_version_constraints": ">=3.10,<3.13",
                        "comfyui_port": comfy_port,
                        "bind_address": "127.0.0.1",
                        "venv_path_policy": {"mode": "tool_folder"},
                    },
                    "planner_service": {
                        "planner_base_url": "http://127.0.0.1:8555",
                        "can_launch_planner_as_sidecar": False,
                        "assistant_repo_path": "",
                        "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                    },
                    "local_planner": {
                        "shared_storage_candidates": [str(planner_model)],
                    },
                    "optional_comfy_models": [],
                },
            )

            fake_comfy = root / "runtime" / "comfyui"
            fake_comfy.mkdir(parents=True, exist_ok=True)
            (fake_comfy / "main.py").write_text(
                (
                    "from __future__ import annotations\n"
                    "import argparse, json\n"
                    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
                    "parser = argparse.ArgumentParser(add_help=False)\n"
                    "parser.add_argument('--listen', default='127.0.0.1')\n"
                    "parser.add_argument('--port', type=int, default=8188)\n"
                    "args, _ = parser.parse_known_args()\n"
                    "class H(BaseHTTPRequestHandler):\n"
                    "    def do_GET(self):\n"
                    "        if self.path == '/system_stats':\n"
                    "            body = json.dumps({'ok': True}).encode('utf-8')\n"
                    "            self.send_response(200)\n"
                    "        elif self.path == '/object_info':\n"
                    "            body = json.dumps({'CheckpointLoaderSimple': {}, 'CLIPTextEncode': {}, 'EmptyLatentImage': {}, 'KSamplerAdvanced': {}, 'VAEDecode': {}, 'SaveImage': {}}).encode('utf-8')\n"
                    "            self.send_response(200)\n"
                    "        else:\n"
                    "            body = b'{}'\n"
                    "            self.send_response(404)\n"
                    "        self.send_header('Content-Type', 'application/json')\n"
                    "        self.send_header('Content-Length', str(len(body)))\n"
                    "        self.end_headers()\n"
                    "        self.wfile.write(body)\n"
                    "    def log_message(self, format, *args):\n"
                    "        return\n"
                    "server = ThreadingHTTPServer((args.listen, args.port), H)\n"
                    "for _ in range(6):\n"
                    "    server.handle_request()\n"
                    "server.server_close()\n"
                ),
                encoding="utf-8",
            )

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "comfyui": {
                        "repo_path": "runtime/comfyui",
                        "python_executable": sys.executable,
                        "port": comfy_port,
                        "bind_address": "127.0.0.1",
                    }
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/setup/verify",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            launched_pid = None
            try:
                with request.urlopen(req, timeout=40) as response:
                    data = json.loads(response.read().decode("utf-8"))
                launched_pid = ((data.get("comfyui") or {}).get("launch") or {}).get("pid")

                self.assertTrue(data["comfyui"]["launched_by_verify"])
                self.assertTrue(data["comfyui"]["health_after"]["ok"])
                self.assertTrue(data["comfyui"]["object_info"]["ok"])
                self.assertTrue(data["planner"]["ready"])
                self.assertTrue(data["planner"]["verify"]["ok"])
                self.assertTrue(data["all_required_ok"])
            finally:
                if launched_pid:
                    self._kill_pid(int(launched_pid))

    def test_setup_verify_endpoint_reports_missing_local_planner_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comfy_server, comfy_thread = self._start_server(_PlannerCapableComfyHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)

            manifest_path = self._write_manifest(
                root,
                {
                    "manifest_version": 1,
                    "comfyui_runtime": {
                        "comfyui_repo_source": "",
                        "local_repo_relative_path": "comfyui",
                        "python_version_constraints": ">=3.10,<3.13",
                        "comfyui_port": comfy_server.server_port,
                        "bind_address": "127.0.0.1",
                        "venv_path_policy": {"mode": "tool_folder"},
                    },
                    "planner_service": {
                        "planner_base_url": "http://127.0.0.1:8555",
                        "can_launch_planner_as_sidecar": False,
                        "assistant_repo_path": "",
                        "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                    },
                    "optional_comfy_models": [],
                },
            )

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "comfyui": {
                        "bind_address": "127.0.0.1",
                        "port": comfy_server.server_port,
                        "health_endpoint": "/system_stats",
                        "object_info_endpoint": "/object_info",
                    },
                    "planner": {
                        "shared_storage_candidates": ["missing-falcon-model"],
                        "expected_storage_dir": "missing-falcon-model",
                        "model_path_env_vars": [],
                        "model_path": "",
                    },
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/setup/verify",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=40) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertFalse(data["planner"]["ready"])
            self.assertEqual(data["planner"]["status"], "missing_model")
            self.assertFalse(data["planner"]["verify"]["ok"])

    def test_planner_service_start_falls_forward_when_default_port_is_occupied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conflict_server, conflict_thread = self._start_server(_NotPlannerHandler)
            self.addCleanup(self._stop_server, conflict_server, conflict_thread)

            assistant_repo = root / "assistant-repo"
            assistant_repo.mkdir(parents=True, exist_ok=True)
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

            manifest_path = self._write_manifest(
                root,
                {
                    "manifest_version": 1,
                    "comfyui_runtime": {
                        "comfyui_repo_source": "",
                        "local_repo_relative_path": "comfyui",
                        "python_version_constraints": ">=3.10,<3.13",
                        "comfyui_port": self._reserve_free_port(),
                        "bind_address": "127.0.0.1",
                        "venv_path_policy": {"mode": "tool_folder"},
                    },
                    "planner_service": {
                        "planner_base_url": f"http://127.0.0.1:{conflict_server.server_port}",
                        "can_launch_planner_as_sidecar": True,
                        "assistant_repo_path": "assistant-repo",
                        "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                    },
                    "optional_comfy_models": [],
                },
            )

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {
                        "assistant_repo_path": "assistant-repo",
                    },
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/service/status", timeout=5) as response:
                status_before = json.loads(response.read().decode("utf-8"))

            self.assertFalse(status_before["port_status"]["conflict"])
            self.assertTrue(status_before["can_start"])

            start_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/service/start",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            launched_pid = None
            try:
                with request.urlopen(start_req, timeout=30) as response:
                    started = json.loads(response.read().decode("utf-8"))
                launched_pid = ((started.get("launch") or {}).get("pid"))

                self.assertTrue(started["ok"])
                self.assertTrue(started["status"]["healthy"])
                self.assertNotEqual(started["status"]["base_url"], f"http://127.0.0.1:{conflict_server.server_port}")
            finally:
                if launched_pid:
                    self._kill_pid(int(launched_pid))

    def test_setup_benchmark_endpoint_persists_linux_recommendation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comfy_server, comfy_thread = self._start_server(_ComfyBenchmarkHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)

            manifest_path = self._write_manifest(
                root,
                {
                    "manifest_version": 1,
                    "comfyui_runtime": {
                        "comfyui_repo_source": "",
                        "local_repo_relative_path": "comfyui",
                        "python_version_constraints": ">=3.10,<3.13",
                        "comfyui_port": comfy_server.server_port,
                        "bind_address": "127.0.0.1",
                        "venv_path_policy": {"mode": "tool_folder"},
                    },
                    "planner_service": {
                        "planner_base_url": "http://127.0.0.1:8555",
                        "can_launch_planner_as_sidecar": False,
                        "assistant_repo_path": "",
                        "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                    },
                    "optional_comfy_models": [],
                },
            )

            (root / "comfyui").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "main.py").write_text("print('comfy')\n", encoding="utf-8")
            (root / "comfyui" / "custom_nodes" / "ComfyUI-LTXVideo").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "models" / "checkpoints").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "models" / "checkpoints" / "ltx-2.3-fp8.safetensors").write_bytes(b"12345678")
            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "comfyui": {
                        "repo_path": "comfyui",
                        "python_executable": sys.executable,
                        "port": comfy_server.server_port,
                        "bind_address": "127.0.0.1",
                    }
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/setup/benchmark",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertIn("recommended_config", data)
            self.assertTrue(data["recommended_config"])
            self.assertIn("artifact_paths", data)
            self.assertTrue(Path(data["artifact_paths"]["latest_path"]).exists())


if __name__ == "__main__":
    unittest.main()
