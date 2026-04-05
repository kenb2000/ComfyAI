import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from prompt_layer.ports import allocate_port, read_registry, record_reservation, repo_port_status
from prompt_layer.setup_config import default_settings, save_settings
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


class TestPorts(unittest.TestCase):
    def setUp(self):
        self._master_ports_path = os.environ.get("MASTER_PORTS_PATH")

    def tearDown(self):
        if self._master_ports_path is None:
            os.environ.pop("MASTER_PORTS_PATH", None)
        else:
            os.environ["MASTER_PORTS_PATH"] = self._master_ports_path

    def _start_server(self, host: str, port: int):
        server = ThreadingHTTPServer((host, port), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _stop_server(self, server, thread):
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    def test_allocator_uses_next_free_port_and_reclaims_stale_assignment(self):
        with tempfile.TemporaryDirectory() as td:
            registry_path = Path(td) / "Projects" / "MasterPorts.json"
            os.environ["MASTER_PORTS_PATH"] = str(registry_path)

            first = allocate_port(
                app_id="repo-a",
                service_name="planner",
                preferred_port=8820,
                host="127.0.0.1",
                allowed_range=(8820, 8823),
                pid=os.getpid(),
            )
            first_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.addCleanup(first_socket.close)
            first_socket.bind(("127.0.0.1", first.assigned_port))
            first_socket.listen(1)
            record_reservation(
                app_id="repo-a",
                service_name="planner",
                protocol="tcp",
                host="127.0.0.1",
                requested_port=8820,
                assigned_port=first.assigned_port,
                pid=os.getpid(),
            )

            second = allocate_port(
                app_id="repo-b",
                service_name="planner",
                preferred_port=8820,
                host="127.0.0.1",
                allowed_range=(8820, 8823),
                pid=os.getpid(),
            )
            self.assertEqual(first.assigned_port, 8820)
            self.assertEqual(second.assigned_port, 8821)

            record_reservation(
                app_id="repo-b",
                service_name="planner",
                protocol="tcp",
                host="127.0.0.1",
                requested_port=8820,
                assigned_port=second.assigned_port,
                pid=os.getpid(),
            )

            record_reservation(
                app_id="repo-b",
                service_name="planner",
                protocol="tcp",
                host="127.0.0.1",
                requested_port=8820,
                assigned_port=second.assigned_port,
                pid=os.getpid(),
                started_at="2020-01-01T00:00:00+00:00",
            )

            reread = read_registry(registry_path)
            self.assertTrue(any(item["assigned_port"] == 8821 for item in reread["stale_entries"]))

            third = allocate_port(
                app_id="repo-b",
                service_name="planner",
                preferred_port=8820,
                host="127.0.0.1",
                allowed_range=(8820, 8823),
                pid=os.getpid(),
            )
            self.assertEqual(third.assigned_port, 8821)

            status = repo_port_status("repo-b", registry_path)
            self.assertEqual(status["entries"][0]["assigned_port"], 8821)

    def test_ports_status_endpoint_reports_repo_registry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            registry_path = root / "Projects" / "MasterPorts.json"
            os.environ["MASTER_PORTS_PATH"] = str(registry_path)

            manifest_dir = root / "requirements"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = manifest_dir / "comfyhybrid_requirements.json"
            manifest_path.write_text(
                json.dumps(
                    {
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
                            "planner_base_url": "http://127.0.0.1:8000",
                            "can_launch_planner_as_sidecar": False,
                            "assistant_repo_path": "",
                            "assistant_repo_path_env_var": "COMFYHYBRID_ASSISTANT_REPO",
                        },
                        "optional_comfy_models": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            save_settings(default_settings(project_root=root, manifest_path=manifest_path), root / "settings.json")

            record_reservation(
                app_id=root.name.lower(),
                service_name="planner",
                protocol="tcp",
                host="127.0.0.1",
                requested_port=8000,
                assigned_port=8002,
                pid=os.getpid(),
            )

            handler = make_setup_status_handler(project_root=root, manifest_path=manifest_path, settings_path=root / "settings.json")
            server, thread = self._start_server("127.0.0.1", 0)
            self._stop_server(server, thread)
            status_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            status_thread = threading.Thread(target=status_server.serve_forever, daemon=True)
            status_thread.start()
            self.addCleanup(self._stop_server, status_server, status_thread)

            with request.urlopen(f"http://127.0.0.1:{status_server.server_port}/ports/status", timeout=5) as response:
                ports_payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(ports_payload["entries"][0]["assigned_port"], 8002)
            self.assertEqual(repo_port_status(root.name.lower(), registry_path)["entries"][0]["assigned_port"], 8002)


if __name__ == "__main__":
    unittest.main()