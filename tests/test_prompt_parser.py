import unittest
from pathlib import Path

from prompt_layer.prompt_to_graph import PromptToGraph


class TestPromptParser(unittest.TestCase):
    def setUp(self) -> None:
        self.ptg = PromptToGraph()

    def test_controlnet_depth_upscale(self):
        prompt = "Generate an SDXL image with ControlNet depth and upscale it"
        graph = self.ptg.parse(prompt)
        self.assertIsInstance(graph, dict)
        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)
        self.assertIn("metadata", graph)
        self.assertIn("prompt", graph["metadata"])
        self.assertIn("name", graph["metadata"])
        self.assertIn("Upscale", graph["metadata"]["name"])  # sanity check

    def test_sdxl_base_refiner(self):
        prompt = "Create SDXL base with refiner"
        graph = self.ptg.parse(prompt)
        self.assertIn("Refiner", graph["metadata"]["name"])

    def test_img2img_lora(self):
        prompt = "Do an img2img with a LoRA style"
        graph = self.ptg.parse(prompt)
        self.assertIn("LoRA", graph["metadata"]["name"])


if __name__ == "__main__":
    unittest.main()
