import tempfile
import unittest
from pathlib import Path

from prompt_layer.planner import LocalPlannerError, LocalPlannerRuntime, build_local_planner_policy
from prompt_layer.planner.config import resolve_local_planner_model_path
from prompt_layer.setup_config import default_settings


FULL_OBJECT_INFO = {
    "CheckpointLoaderSimple": {},
    "CLIPTextEncode": {},
    "LoadImage": {},
    "ControlNetLoader": {},
    "ControlNetApplyAdvanced": {},
    "EmptyLatentImage": {},
    "KSampler": {},
    "KSamplerAdvanced": {},
    "LatentUpscaleBy": {},
    "VAEDecode": {},
    "SaveImage": {},
    "LoraLoader": {},
    "VAEEncode": {},
}


class TestLocalPlanner(unittest.TestCase):
    def _write_fake_model(self, root: Path) -> Path:
        model_dir = root / "shared-models" / "Falcon3-10B-Instruct-1.58bit"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (model_dir / "model.safetensors").write_bytes(b"12345678")
        return model_dir

    def test_planner_config_loads_local_falcon_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = self._write_fake_model(root)
            settings = default_settings(project_root=root)
            settings["planner"]["shared_storage_candidates"] = [str(model_dir)]
            policy = build_local_planner_policy(settings, root)
            self.assertTrue(policy["enabled"])
            self.assertEqual(policy["mode"], "local")
            self.assertEqual(policy["platform_target"], "linux")
            self.assertEqual(policy["default_model_id"], "tiiuae/Falcon3-10B-Instruct-1.58bit")
            self.assertEqual(policy["role_mapping"]["dispatcher_model"], policy["default_model_id"])

    def test_planner_model_is_registered_from_shared_storage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = self._write_fake_model(root)
            settings = default_settings(project_root=root)
            settings["planner"]["shared_storage_candidates"] = [str(model_dir)]
            resolved, source, inspection = resolve_local_planner_model_path(settings, root)
            self.assertEqual(resolved, model_dir)
            self.assertEqual(source, "settings:shared_storage_candidates")
            self.assertTrue(inspection["ok"])

    def test_simple_plan_generation_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = self._write_fake_model(root)
            settings = default_settings(project_root=root)
            settings["planner"]["shared_storage_candidates"] = [str(model_dir)]
            runtime = LocalPlannerRuntime(settings, root)
            result = runtime.plan(
                {"prompt": "Create SDXL base with refiner", "workflow_profile": "preview"},
                object_info_payload=FULL_OBJECT_INFO,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["selected_template"], "sdxl_base_refiner")
            self.assertTrue(result["validation"]["ok"])
            self.assertIn("workflow_json", result)

    def test_validation_success_path_repairs_to_supported_template(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = self._write_fake_model(root)
            settings = default_settings(project_root=root)
            settings["planner"]["shared_storage_candidates"] = [str(model_dir)]
            runtime = LocalPlannerRuntime(settings, root)
            limited_object_info = {
                "CheckpointLoaderSimple": {},
                "CLIPTextEncode": {},
                "EmptyLatentImage": {},
                "KSamplerAdvanced": {},
                "VAEDecode": {},
                "SaveImage": {},
            }
            result = runtime.plan(
                {"prompt": "Generate an SDXL image with ControlNet depth and upscale it"},
                object_info_payload=limited_object_info,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["selected_template"], "sdxl_base_refiner")
            self.assertGreaterEqual(result["repair_count"], 1)

    def test_bounded_repair_failure_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = self._write_fake_model(root)
            settings = default_settings(project_root=root)
            settings["planner"]["shared_storage_candidates"] = [str(model_dir)]
            settings["planner"]["max_repairs_before_fail"] = 2
            runtime = LocalPlannerRuntime(settings, root)
            with self.assertRaises(LocalPlannerError):
                runtime.plan(
                    {"prompt": "Create SDXL base with refiner"},
                    object_info_payload={"CheckpointLoaderSimple": {}},
                )


if __name__ == "__main__":
    unittest.main()
