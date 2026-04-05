import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from prompt_layer.setup_config import default_settings, deep_merge, save_settings
from prompt_layer.setup_status import collect_setup_status
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


class TestSetupStatus(unittest.TestCase):
    def _start_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _stop_server(self, server, thread):
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def _write_manifest(self, root: Path, planner_url: str, comfy_port: int) -> Path:
        manifest_dir = root / "requirements"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "comfyhybrid_requirements.json"
        manifest = {
            "manifest_version": 1,
            "comfyui_runtime": {
                "comfyui_repo_source": "https://github.com/comfyanonymous/ComfyUI.git",
                "local_repo_relative_path": "comfyui",
                "python_version_constraints": ">=3.10,<3.13",
                "comfyui_port": comfy_port,
                "bind_address": "127.0.0.1",
                "venv_path_policy": {
                    "mode": "inside_repo",
                    "inside_repo_relative_path": ".venv",
                    "tool_folder_relative_path": "tools/.venv",
                },
            },
            "planner_service": {
                "planner_base_url": planner_url,
                "can_launch_planner_as_sidecar": False,
                "assistant_repo_path": "assistant_repo",
                "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
            },
            "optional_comfy_models": [
                {
                    "model_name": "sdxl_base.safetensors",
                    "type": "checkpoint",
                    "source": {"kind": "local_path", "value": "downloads/sdxl_base.safetensors"},
                    "expected_size_min_bytes": 8,
                    "sha256": None,
                    "target_path_relative_to_models": "checkpoints/sdxl_base.safetensors",
                },
                {
                    "model_name": "controlnet_depth.safetensors",
                    "type": "controlnet",
                    "source": {"kind": "local_path", "value": ""},
                    "expected_size_min_bytes": 8,
                    "sha256": None,
                    "target_path_relative_to_models": "controlnet/controlnet_depth.safetensors",
                },
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path

    def test_collect_setup_status_reports_runtime_planner_and_models(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            comfy_server, comfy_thread = self._start_server(_JsonHandler)
            planner_server, planner_thread = self._start_server(_JsonHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)
            self.addCleanup(self._stop_server, planner_server, planner_thread)

            manifest_path = self._write_manifest(
                root,
                f"http://127.0.0.1:{planner_server.server_port}",
                comfy_server.server_port,
            )

            (root / "assistant_repo").mkdir(parents=True, exist_ok=True)
            (root / "downloads").mkdir(parents=True, exist_ok=True)
            (root / "downloads" / "sdxl_base.safetensors").write_bytes(b"12345678")

            (root / "comfyui" / "models" / "checkpoints").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "models" / "controlnet").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "main.py").write_text("print('comfy')\n", encoding="utf-8")
            (root / "comfyui" / "models" / "checkpoints" / "sdxl_base.safetensors").write_bytes(b"12345678")

            python_exe = root / ".venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.write_text("", encoding="utf-8")

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "tool_paths": {"venv_dir": ".venv"},
                },
            )
            save_settings(settings, root / "settings.json")

            status = collect_setup_status(manifest_path=manifest_path, settings_path=root / "settings.json", project_root=root)

            self.assertTrue(status["comfyui"]["installed"])
            self.assertTrue(status["comfyui"]["runnable"])
            self.assertTrue(status["comfyui"]["reachable"])
            self.assertTrue(status["planner"]["reachable"])
            self.assertTrue(status["planner"]["assistant_repo_exists"])
            self.assertFalse(status["comfyui"]["port_status"]["conflict"])
            self.assertFalse(status["planner"]["port_status"]["conflict"])
            self.assertIn("ports", status)

            models = {item["model_name"]: item for item in status["optional_models"]["items"]}
            self.assertEqual(models["sdxl_base.safetensors"]["status"], "present")
            self.assertEqual(models["controlnet_depth.safetensors"]["status"], "missing")
            self.assertEqual(status["optional_models"]["present_count"], 1)
            self.assertEqual(status["optional_models"]["missing_count"], 1)

    def test_setup_status_endpoint_returns_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            comfy_server, comfy_thread = self._start_server(_JsonHandler)
            planner_server, planner_thread = self._start_server(_JsonHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)
            self.addCleanup(self._stop_server, planner_server, planner_thread)

            manifest_path = self._write_manifest(
                root,
                f"http://127.0.0.1:{planner_server.server_port}",
                comfy_server.server_port,
            )

            (root / "comfyui" / "models" / "checkpoints").mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "main.py").write_text("print('comfy')\n", encoding="utf-8")
            python_exe = root / ".venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True, exist_ok=True)
            python_exe.write_text("", encoding="utf-8")

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "tool_paths": {"venv_dir": ".venv"},
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            status_server, status_thread = self._start_server(handler)
            self.addCleanup(self._stop_server, status_server, status_thread)

            with request.urlopen(
                f"http://127.0.0.1:{status_server.server_port}/setup/status",
                timeout=5,
            ) as response:
                status_code = response.getcode()
                data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(status_code, 200)
            self.assertIn("comfyui", data)
            self.assertIn("planner", data)
            self.assertIn("optional_models", data)
            self.assertIn("port_status", data["comfyui"])
            self.assertIn("port_status", data["planner"])
            self.assertIn("ports", data)


if __name__ == "__main__":
    unittest.main()
