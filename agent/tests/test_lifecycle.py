"""End-to-end: a real workspace, real subprocesses, real deletions."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from selfmod import lineage, orchestrator
from selfmod.sandbox import MARKER

SOURCE = orchestrator.source_root()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "workspace"
        orchestrator.init(self.workspace)
        self.ledger = lineage.Ledger(self.workspace)

    def tearDown(self):
        self._tmp.cleanup()

    def gen_dir(self, name: str) -> Path:
        return self.workspace / "generations" / name

    def advance_to_upgrade(self, limit: int = 10) -> dict:
        """Run cycles until a child is actually kept.

        Mutation is random within a seeded lineage, so most cycles reject
        their child; the tests care about the cycle that does not.
        """
        for cycle in range(limit):
            outcome = orchestrator.run_cycle(self.workspace, task="pathfind",
                                             seed=7, cycle=cycle)
            if outcome["action"] == "upgraded":
                return outcome
        self.fail(f"no upgrade within {limit} cycles")

    # -- seeding ----------------------------------------------------------
    def test_init_seeds_one_marked_generation(self):
        self.assertTrue((self.gen_dir("gen-0001") / MARKER).is_file())
        self.assertTrue((self.gen_dir("gen-0001") / "selfmod" / "agent.py").is_file())
        self.assertEqual(self.ledger.head().name, "gen-0001")

    def test_init_refuses_to_reseed_without_force(self):
        again = orchestrator.init(self.workspace)
        self.assertFalse(again["created"])
        self.assertEqual(again["head"], "gen-0001")

    # -- success path -----------------------------------------------------
    def test_success_writes_a_child_whose_source_differs(self):
        outcome = self.advance_to_upgrade()
        child = self.gen_dir(outcome["child"])
        self.assertTrue(child.is_dir())
        parent_src = (self.gen_dir("gen-0001") / "selfmod" / "genome.py").read_text()
        child_src = (child / "selfmod" / "genome.py").read_text()
        self.assertNotEqual(parent_src, child_src)
        self.assertIn("GENERATION = 2", child_src)
        self.assertIn("ANCESTRY = 'seed>gen-0001'", child_src)

    def test_the_kept_child_becomes_the_head_and_the_parent_survives(self):
        outcome = self.advance_to_upgrade()
        self.assertEqual(self.ledger.head().name, outcome["child"])
        self.assertTrue(self.gen_dir("gen-0001").is_dir())

    def test_a_child_that_does_not_improve_is_deleted(self):
        actions = []
        for cycle in range(6):
            outcome = orchestrator.run_cycle(self.workspace, task="pathfind",
                                             seed=7, cycle=cycle)
            actions.append(outcome["action"])
            if outcome["action"] == "child-rejected":
                self.assertFalse(self.gen_dir(outcome["child"]).exists())
                self.assertTrue(outcome["deletion"]["performed"])
                break
        self.assertIn("child-rejected", actions)

    def test_scores_never_regress_across_a_run(self):
        for cycle in range(8):
            orchestrator.run_cycle(self.workspace, task="pathfind", seed=7,
                                   cycle=cycle)
        scores = [g.score for g in self.ledger.living() if g.score is not None]
        self.assertEqual(scores, sorted(scores))

    # -- failure path -----------------------------------------------------
    def test_failure_deletes_the_generation_and_leaves_a_tombstone(self):
        outcome = orchestrator.run_cycle(self.workspace, task="impossible")
        self.assertEqual(outcome["action"], "terminated")
        self.assertFalse(self.gen_dir("gen-0001").exists())
        self.assertTrue(self.ledger.path.is_file())
        events = [e["event"] for e in self.ledger.entries()
                  if e["generation"] == "gen-0001"]
        self.assertIn(lineage.TERMINATED, events)
        self.assertIsNone(self.ledger.head())

    def test_failure_rolls_the_lineage_back_to_the_parent(self):
        upgrade = self.advance_to_upgrade()
        child = upgrade["child"]
        self.assertEqual(self.ledger.head().name, child)
        outcome = orchestrator.run_cycle(self.workspace, task="impossible")
        self.assertEqual(outcome["generation"], child)
        self.assertFalse(self.gen_dir(child).exists())
        self.assertEqual(self.ledger.head().name, "gen-0001")
        self.assertTrue(self.gen_dir("gen-0001").is_dir())

    def test_an_unseeded_workspace_says_so(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            outcome = orchestrator.run_cycle(Path(tmp) / "fresh")
        self.assertEqual(outcome["action"], "uninitialised")

    def test_an_extinct_lineage_stops_cleanly(self):
        orchestrator.run_cycle(self.workspace, task="impossible")
        outcome = orchestrator.run_cycle(self.workspace, task="impossible")
        self.assertEqual(outcome["action"], "extinct")

    def test_dry_run_deletes_nothing_and_keeps_the_head(self):
        outcome = orchestrator.run_cycle(self.workspace, task="impossible",
                                         armed=False)
        self.assertEqual(outcome["action"], "termination-simulated")
        self.assertTrue(self.gen_dir("gen-0001").is_dir())
        self.assertEqual(self.ledger.head().name, "gen-0001")

    # -- the source tree is not part of the sandbox -----------------------
    def test_the_repository_copy_refuses_to_delete_itself(self):
        proc = subprocess.run(
            [sys.executable, "-m", "selfmod", "cycle", "--task", "impossible",
             "--home", str(SOURCE), "--workspace", str(self.workspace)],
            cwd=str(SOURCE), capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("spared", proc.stdout)
        self.assertTrue((SOURCE / "selfmod" / "agent.py").is_file())

    def test_a_generation_cannot_reach_outside_the_workspace(self):
        victim = Path(self._tmp.name) / "precious"
        victim.mkdir()
        (victim / "keep.txt").write_text("hello")
        shutil.copy(self.gen_dir("gen-0001") / MARKER, victim / MARKER)
        from selfmod.sandbox import Sandbox
        report = Sandbox(self.workspace).delete_tree(victim, armed=True)
        self.assertFalse(report.performed)
        self.assertTrue((victim / "keep.txt").is_file())

    # -- a task that cannot be judged -------------------------------------
    def test_an_unjudgeable_task_deletes_nothing(self):
        from selfmod import tasks
        from selfmod.agent import Agent
        from selfmod.errors import TaskUnavailable

        class Abstains(tasks.Task):
            name = "abstains"

            def run(self, params, *, seed=0, clauses=()):
                raise TaskUnavailable("no credentials")

        tasks.REGISTRY["abstains"] = Abstains()
        try:
            agent = Agent(self.gen_dir("gen-0001"), self.workspace,
                          task="abstains", seed=7)
            outcome = agent.live()
        finally:
            del tasks.REGISTRY["abstains"]

        self.assertEqual(outcome.action, "task-unavailable")
        self.assertTrue(self.gen_dir("gen-0001").is_dir())
        self.assertEqual(self.ledger.head().name, "gen-0001")
        events = [e["event"] for e in self.ledger.entries()]
        self.assertNotIn(lineage.TERMINATED, events)

    # -- the prompt-evolving task -----------------------------------------
    def test_the_llm_lineage_evolves_its_prompt(self):
        from selfmod import genome as seed_genome

        for cycle in range(14):
            outcome = orchestrator.run_cycle(self.workspace, task="llm", seed=7,
                                             cycle=cycle)
            self.assertNotEqual(outcome["action"], "task-unavailable",
                                outcome.get("detail"))
            if outcome["action"] == "upgraded":
                child_src = (self.gen_dir(outcome["child"]) / "selfmod"
                             / "genome.py").read_text()
                namespace: dict = {}
                exec(compile(child_src, "child", "exec"), namespace)
                changed = (namespace["PROMPT_CLAUSES"]
                           != list(seed_genome.PROMPT_CLAUSES)
                           or namespace["PARAMS"] != dict(seed_genome.PARAMS))
                self.assertTrue(changed, "an upgrade changed nothing")
                return
        self.fail("no upgrade on the llm task within 14 cycles")

    def test_the_llm_lineage_shares_one_cache_above_the_generations(self):
        orchestrator.run_cycle(self.workspace, task="llm", seed=7)
        cache = self.workspace / "llm-cache"
        self.assertTrue(cache.is_dir())
        self.assertTrue(any(cache.iterdir()))

    # -- reporting --------------------------------------------------------
    def test_status_and_tree_describe_the_lineage(self):
        self.advance_to_upgrade()
        info = orchestrator.status(self.workspace)
        self.assertEqual(info["missing_from_disk"], [])
        self.assertEqual(info["orphaned_on_disk"], [])
        self.assertGreaterEqual(info["generations_recorded"], 2)
        tree = self.ledger.render_tree()
        self.assertIn("gen-0001", tree)
        self.assertIn("gen-0002", tree)

    def test_selfcheck_reports_a_json_verdict(self):
        proc = subprocess.run(
            [sys.executable, "-m", "selfmod", "selfcheck", "--json"],
            cwd=str(SOURCE), capture_output=True, text=True,
        )
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertIn("score", payload)

    def test_a_head_that_cannot_run_is_terminated_by_the_supervisor(self):
        # Stands in for a mutation that produces source the child cannot run.
        upgrade = self.advance_to_upgrade()
        child = upgrade["child"]
        (self.gen_dir(child) / "selfmod" / "genome.py").write_text(
            "this is not python\n")
        follow_up = orchestrator.run_cycle(self.workspace, task="pathfind", seed=7)
        self.assertEqual(follow_up["action"], "terminated-by-supervisor")
        self.assertFalse(self.gen_dir(child).exists())
        self.assertEqual(self.ledger.head().name, "gen-0001")

    def test_a_child_whose_own_check_crashes_is_never_promoted(self):
        from selfmod.agent import Agent
        agent = Agent(self.gen_dir("gen-0001"), self.workspace, task="pathfind",
                      seed=7)
        broken = self.workspace / "generations" / "gen-9999"
        (broken / "selfmod").mkdir(parents=True)
        (broken / "selfmod" / "__init__.py").write_text("raise RuntimeError('boom')")
        check = agent.validate(broken)
        self.assertFalse(check["ok"])
        self.assertIn("crash", check["detail"])


if __name__ == "__main__":
    unittest.main()
