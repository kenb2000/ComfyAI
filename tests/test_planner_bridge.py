import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from prompt_layer.setup_config import default_settings, deep_merge, save_settings
from prompt_layer.setup_status_server import make_setup_status_handler


class _FakeComfyHandler(BaseHTTPRequestHandler):
    queued_payloads = []

    def _write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):  # noqa: N802
        if self.path == "/system_stats":
            self._write_json({"ok": True})
            return
        if self.path == "/object_info":
            self._write_json(
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
            )
            return
        self._write_json({"error": "not_found"}, status=404)

    def do_POST(self):  # noqa: N802
        if self.path == "/prompt":
            type(self).queued_payloads.append(self._read_json())
            self._write_json({"prompt_id": "planner-queued"})
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

    def _write_manifest(self, root: Path, comfy_port: int) -> Path:
        manifest_dir = root / "requirements"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "comfyhybrid_requirements.json"
        manifest = {
            "manifest_version": 1,
            "comfyui_runtime": {
                "comfyui_repo_source": "",
                "local_repo_relative_path": "comfyui",
                "python_version_constraints": ">=3.10,<3.13",
                "comfyui_port": comfy_port,
                "bind_address": "127.0.0.1",
                "venv_path_policy": {"mode": "tool_folder"},
            },
            "planner_service": {
                "planner_base_url": "http://127.0.0.1:8000",
                "health_endpoint": "/health",
                "can_launch_planner_as_sidecar": True,
                "assistant_repo_path": "",
                "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
            },
            "optional_comfy_models": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest_path

    def _write_fake_planner_model(self, root: Path) -> Path:
        model_dir = root / "shared-models" / "Falcon3-10B-Instruct-1.58bit"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model.safetensors").write_bytes(b"12345678")
        return model_dir

    def test_local_planner_ui_and_policy_routes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comfy_server, comfy_thread = self._start_server(_FakeComfyHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)
            manifest_path = self._write_manifest(root, comfy_server.server_port)
            planner_model = self._write_fake_planner_model(root)

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {
                        "shared_storage_candidates": [str(planner_model)],
                    },
                    "comfyui": {
                        "port": comfy_server.server_port,
                        "bind_address": "127.0.0.1",
                        "health_endpoint": "/system_stats",
                        "object_info_endpoint": "/object_info",
                    },
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/ui", timeout=5) as response:
                html = response.read().decode("utf-8")
            self.assertIn("Local Planner: Falcon 10B 1.58", html)
            self.assertIn("Verify Planner", html)
            self.assertIn("Rebuild Planner Runtime", html)
            self.assertIn("Generate Plan", html)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/models", timeout=5) as response:
                models = json.loads(response.read().decode("utf-8"))
            self.assertEqual(models["default_model"]["id"], "tiiuae/Falcon3-10B-Instruct-1.58bit")
            self.assertTrue(models["default_model"]["present"])

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/planner/policy", timeout=5) as response:
                policy = json.loads(response.read().decode("utf-8"))
            self.assertEqual(policy["mode"], "local")
            self.assertTrue(policy["model_present"])

            policy_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/policy",
                data=json.dumps({"request_timeout_seconds": 45}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(policy_req, timeout=5) as response:
                updated_policy = json.loads(response.read().decode("utf-8"))
            self.assertEqual(updated_policy["request_timeout_seconds"], 45)

    def test_planner_rebuild_verify_and_helper_process(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            comfy_server, comfy_thread = self._start_server(_FakeComfyHandler)
            self.addCleanup(self._stop_server, comfy_server, comfy_thread)
            manifest_path = self._write_manifest(root, comfy_server.server_port)
            planner_model = self._write_fake_planner_model(root)
            (root / "comfyui" / "main.py").parent.mkdir(parents=True, exist_ok=True)
            (root / "comfyui" / "main.py").write_text("print('comfy')\n", encoding="utf-8")

            settings = deep_merge(
                default_settings(project_root=root, manifest_path=manifest_path),
                {
                    "planner": {
                        "shared_storage_candidates": [str(planner_model)],
                        "model_path": "",
                    },
                    "comfyui": {
                        "repo_path": "comfyui",
                        "port": comfy_server.server_port,
                        "bind_address": "127.0.0.1",
                        "health_endpoint": "/system_stats",
                        "object_info_endpoint": "/object_info",
                    },
                },
            )
            save_settings(settings, root / "settings.json")

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server(handler)
            self.addCleanup(self._stop_server, server, thread)

            rebuild_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/rebuild",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(rebuild_req, timeout=15) as response:
                rebuild = json.loads(response.read().decode("utf-8"))
            self.assertTrue(rebuild["planner"]["model_present"])

            verify_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/planner/verify",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(verify_req, timeout=15) as response:
                verify = json.loads(response.read().decode("utf-8"))
            self.assertTrue(verify["verify"]["ok"])

            helper_req = request.Request(
                f"http://127.0.0.1:{server.server_port}/helper/process",
                data=json.dumps(
                    {
                        "prompt": "Create SDXL base with refiner",
                        "workflow_profile": "preview",
                        "queue_workflow": True,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(helper_req, timeout=15) as response:
                lines = [json.loads(line) for line in response.read().decode("utf-8").splitlines() if line.strip()]

            event_names = [line.get("event") for line in lines]
            self.assertIn("linux_runtime_plan", event_names)
            self.assertIn("progress", event_names)
            self.assertIn("done", event_names)
            self.assertIn("workflow_saved", event_names)
            self.assertTrue(_FakeComfyHandler.queued_payloads)

            with request.urlopen(f"http://127.0.0.1:{server.server_port}/workspace/workflows", timeout=5) as response:
                workflows = json.loads(response.read().decode("utf-8"))
            self.assertEqual(workflows["count"], 1)
            saved_file = root / workflows["items"][0]["relative_path"]
            self.assertTrue(saved_file.exists())


if __name__ == "__main__":
    unittest.main()
