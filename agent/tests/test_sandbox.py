"""The containment rules are the only thing standing between a failed task
and an arbitrary rmtree, so they get the most direct tests."""

import tempfile
import unittest
from pathlib import Path

from selfmod.sandbox import MARKER, PROTECT, Sandbox, SandboxError


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "workspace"
        self.root.mkdir()
        self.sandbox = Sandbox(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _generation(self, name="gen-0001") -> Path:
        target = self.root / "generations" / name
        (target / "selfmod").mkdir(parents=True)
        (target / MARKER).write_text("{}\n")
        (target / "selfmod" / "genome.py").write_text("PARAMS = {}\n")
        return target

    def test_deletes_a_marked_generation(self):
        target = self._generation()
        report = self.sandbox.delete_tree(target, armed=True, reason="failed")
        self.assertTrue(report.performed)
        self.assertFalse(target.exists())
        self.assertEqual(report.files, 2)

    def test_dry_run_leaves_the_directory(self):
        target = self._generation()
        report = self.sandbox.delete_tree(target, armed=False)
        self.assertFalse(report.performed)
        self.assertTrue(target.exists())
        self.assertIn("dry run", report.notes[0])

    def test_refuses_directory_without_marker(self):
        target = self.root / "generations" / "gen-0002"
        target.mkdir(parents=True)
        report = self.sandbox.delete_tree(target, armed=True)
        self.assertFalse(report.performed)
        self.assertIn(MARKER, report.refusal)
        self.assertTrue(target.exists())

    def test_refuses_protected_directory(self):
        target = self._generation()
        (target / PROTECT).touch()
        report = self.sandbox.delete_tree(target, armed=True)
        self.assertFalse(report.performed)
        self.assertTrue(target.exists())

    def test_refuses_the_root_itself(self):
        (self.root / MARKER).write_text("{}\n")
        report = self.sandbox.delete_tree(self.root, armed=True)
        self.assertFalse(report.performed)
        self.assertIn("root", report.refusal)
        self.assertTrue(self.root.exists())

    def test_refuses_paths_outside_the_sandbox(self):
        outside = Path(self._tmp.name) / "precious"
        (outside / "selfmod").mkdir(parents=True)
        (outside / MARKER).write_text("{}\n")
        report = self.sandbox.delete_tree(outside, armed=True)
        self.assertFalse(report.performed)
        self.assertIn("outside sandbox", report.refusal)
        self.assertTrue(outside.exists())

    def test_refuses_traversal_out_of_the_sandbox(self):
        outside = Path(self._tmp.name) / "precious"
        (outside / "selfmod").mkdir(parents=True)
        (outside / MARKER).write_text("{}\n")
        escape = self.root / "generations" / ".." / ".." / "precious"
        report = self.sandbox.delete_tree(escape, armed=True)
        self.assertFalse(report.performed)
        self.assertTrue(outside.exists())

    def test_refuses_symlink_to_a_valid_generation(self):
        target = self._generation()
        link = self.root / "generations" / "link"
        link.symlink_to(target, target_is_directory=True)
        report = self.sandbox.delete_tree(link, armed=True)
        self.assertFalse(report.performed)
        self.assertIn("symlink", report.refusal)
        self.assertTrue(target.exists())

    def test_require_inside_rejects_escapes(self):
        with self.assertRaises(SandboxError):
            self.sandbox.require_inside(Path(self._tmp.name) / "elsewhere")


if __name__ == "__main__":
    unittest.main()
