"""A generation upgrades by rewriting source, so the rewrite has to be exact."""

import tempfile
import unittest
from pathlib import Path

from selfmod import genome


class GenomeTests(unittest.TestCase):
    def _copy(self) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "genome.py"
        tmp.write_text(Path(genome.__file__).read_text(encoding="utf-8"),
                       encoding="utf-8")
        return tmp

    def test_rewrite_produces_importable_source_with_new_values(self):
        path = self._copy()
        genome.rewrite(path, generation=4, ancestry="seed>gen-0003",
                       params={"beam_width": 9, "heuristic_weight": 1.75,
                               "step_budget": 640, "retry_limit": 2,
                               "tie_breaker": 0.25})
        namespace: dict = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
        self.assertEqual(namespace["GENERATION"], 4)
        self.assertEqual(namespace["ANCESTRY"], "seed>gen-0003")
        self.assertEqual(namespace["PARAMS"]["beam_width"], 9)
        self.assertEqual(namespace["PARAMS"]["tie_breaker"], 0.25)

    def test_rewrite_is_idempotent_in_shape(self):
        path = self._copy()
        params = dict(genome.PARAMS)
        genome.rewrite(path, generation=2, ancestry="a", params=params)
        once = path.read_text(encoding="utf-8")
        genome.rewrite(path, generation=2, ancestry="a", params=params)
        self.assertEqual(once, path.read_text(encoding="utf-8"))

    def test_rewrite_keeps_the_rest_of_the_module(self):
        path = self._copy()
        genome.rewrite(path, generation=2, ancestry="a", params=dict(genome.PARAMS))
        text = path.read_text(encoding="utf-8")
        self.assertIn("def rewrite(", text)
        self.assertIn("BOUNDS", text)
        starts = [ln for ln in text.splitlines() if ln.startswith(genome.BEGIN)]
        self.assertEqual(len(starts), 1)

    def test_clamp_respects_bounds_and_integrality(self):
        clamped = genome.clamp({"beam_width": 999.6, "heuristic_weight": -3.0,
                                "step_budget": 12.4, "tie_breaker": 0.123456})
        self.assertEqual(clamped["beam_width"], 64)
        self.assertEqual(clamped["heuristic_weight"], 0.5)
        self.assertEqual(clamped["step_budget"], 40)
        self.assertIsInstance(clamped["step_budget"], int)
        self.assertEqual(clamped["tie_breaker"], 0.1235)

    def test_rewrite_rejects_a_file_without_a_block(self):
        path = Path(tempfile.mkdtemp()) / "plain.py"
        path.write_text("PARAMS = {}\n")
        with self.assertRaises(ValueError):
            genome.rewrite(path, generation=1, ancestry="x", params={})


if __name__ == "__main__":
    unittest.main()
