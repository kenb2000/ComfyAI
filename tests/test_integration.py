import json
import tempfile
import unittest
from pathlib import Path

from prompt_layer import cli


class TestIntegrationCLI(unittest.TestCase):
    def test_cli_generates_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "graph.json"
            code = cli.main(["Generate an SDXL image with ControlNet depth and upscale it", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("nodes", data)
            self.assertIn("edges", data)
            self.assertIn("metadata", data)


if __name__ == "__main__":
    unittest.main()
