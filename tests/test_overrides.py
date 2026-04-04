import unittest
from prompt_layer.prompt_to_graph import PromptToGraph


class TestOverrides(unittest.TestCase):
    def setUp(self) -> None:
        self.ptg = PromptToGraph()

    def test_sampler_scheduler_overrides(self):
        g = self.ptg.parse("Create SDXL base with refiner")
        g2 = self.ptg.apply_overrides(g, {"sampler": "ddim", "scheduler": "karras"})
        samplers = [n for n in g2["nodes"] if n.get("class_type") in ("KSampler", "KSamplerAdvanced")]
        self.assertTrue(any(n.get("inputs", {}).get("sampler_name") == "ddim" for n in samplers))
        self.assertTrue(any(n.get("inputs", {}).get("scheduler") == "karras" for n in samplers))

    def test_refiner_split(self):
        g = self.ptg.parse("Create SDXL base with refiner")
        g2 = self.ptg.apply_overrides(g, {"refiner_split": 12})
        adv = [n for n in g2["nodes"] if n.get("class_type") == "KSamplerAdvanced"]
        if len(adv) >= 2:
            base, ref = adv[0], adv[1]
            self.assertEqual(base.get("inputs", {}).get("end_at_step"), 12)
            self.assertEqual(ref.get("inputs", {}).get("start_at_step"), 12)


if __name__ == "__main__":
    unittest.main()
