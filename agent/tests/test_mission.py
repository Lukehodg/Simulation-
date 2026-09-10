"""The `do` mode: one instruction, deterministic checks, delete on failure."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from selfmod import mission as mission_mod
from selfmod import orchestrator, tasks
from selfmod.errors import TaskUnavailable
from selfmod.mission import Check, Mission, MissionTask

PARAMS = {"llm_effort": 1, "llm_answer_tokens": 1024}


class CheckTests(unittest.TestCase):
    def test_contains_is_case_insensitive(self):
        self.assertTrue(Check("contains", "Motorway").run("the motorway hums")[0])
        self.assertFalse(Check("contains", "zebra").run("the motorway hums")[0])

    def test_not_contains_catches_banned_words(self):
        ok, note = Check("not_contains", "sorry").run("Sorry, I cannot")
        self.assertFalse(ok)
        self.assertIn("sorry", note)

    def test_word_limits_report_the_count(self):
        ok, note = Check("max_words", "3").run("one two three four")
        self.assertFalse(ok)
        self.assertEqual(note, "4 words")
        self.assertTrue(Check("min_words", "2").run("one two")[0])

    def test_json_check_rejects_prose(self):
        self.assertTrue(Check("json").run('{"a": 1}')[0])
        ok, note = Check("json").run("here is your JSON")
        self.assertFalse(ok)
        self.assertIn("not JSON", note)

    def test_regex_check_and_bad_pattern(self):
        self.assertTrue(Check("matches", r"^\d{4}-\d{2}").run("2024-02-03")[0])
        with self.assertRaises(mission_mod.MissionError):
            Check("matches", "([unclosed").run("x")

    def test_empty_reply_fails_the_default_check(self):
        self.assertFalse(Check("non_empty").run("   ")[0])

    def test_shell_check_sees_the_reply_in_a_file(self):
        ok, _ = Check("shell", "grep -q motorway {output}").run("the motorway")
        self.assertTrue(ok)
        ok, note = Check("shell", "grep -q zebra {output}").run("the motorway")
        self.assertFalse(ok)
        self.assertIn("exit 1", note)

    def test_an_unknown_check_is_refused(self):
        with self.assertRaises(mission_mod.MissionError):
            Check("vibes", "good").run("anything")


class MissionFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"

    def tearDown(self):
        os.environ.pop(mission_mod.ENV_MISSION, None)
        self._tmp.cleanup()

    def test_a_mission_survives_a_round_trip(self):
        mission = Mission("do the thing", [Check("contains", "thing")],
                          judge=True, survive_at=0.5)
        path = mission.save(Path(self._tmp.name) / "mission.json")
        os.environ[mission_mod.ENV_MISSION] = str(path)
        loaded = mission_mod.load()
        self.assertEqual(loaded.to_dict(), mission.to_dict())
        self.assertIn("do the thing", loaded.describe())

    def test_describe_states_the_rule(self):
        strict = Mission("x", [Check("non_empty")]).describe()
        self.assertIn("every check must pass", strict)
        self.assertIn("deletes itself", strict)
        lenient = Mission("x", [Check("non_empty")], survive_at=0.5).describe()
        self.assertIn("50%", lenient)

    def test_no_mission_means_abstain_not_fail(self):
        os.environ.pop(mission_mod.ENV_MISSION, None)
        with self.assertRaises(TaskUnavailable):
            MissionTask().run(PARAMS, clauses=[0])

    def test_a_missing_mission_file_is_reported(self):
        os.environ[mission_mod.ENV_MISSION] = str(Path(self._tmp.name) / "gone")
        with self.assertRaises(mission_mod.MissionError):
            mission_mod.load()


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"
        os.environ.pop("SELFMOD_LLM_CACHE", None)

    def tearDown(self):
        os.environ.pop(mission_mod.ENV_MISSION, None)
        self._tmp.cleanup()

    def _mission(self, **kwargs) -> None:
        mission = Mission(**kwargs)
        path = mission.save(Path(self._tmp.name) / "mission.json")
        os.environ[mission_mod.ENV_MISSION] = str(path)

    def test_an_unmet_check_fails_the_generation(self):
        self._mission(instruction="write something",
                      checks=[Check("contains", "zebra")])
        verdict = MissionTask().run(PARAMS, clauses=[0])
        self.assertFalse(verdict.passed)
        self.assertIn("zebra", verdict.detail)

    def test_a_met_check_passes(self):
        self._mission(instruction="write something",
                      checks=[Check("contains", "something")])
        self.assertTrue(MissionTask().run(PARAMS, clauses=[0]).passed)

    def test_partial_credit_only_survives_when_allowed(self):
        checks = [Check("contains", "something"), Check("contains", "zebra")]
        self._mission(instruction="write something", checks=checks)
        self.assertFalse(MissionTask().run(PARAMS, clauses=[0]).passed)
        self._mission(instruction="write something", checks=checks,
                      survive_at=0.5)
        self.assertTrue(MissionTask().run(PARAMS, clauses=[0]).passed)

    def test_the_score_tracks_how_many_checks_passed(self):
        self._mission(instruction="write something",
                      checks=[Check("contains", "something"),
                              Check("contains", "zebra")],
                      survive_at=0.5)
        half = MissionTask().run(PARAMS, clauses=[0]).score
        self._mission(instruction="write something",
                      checks=[Check("contains", "something"),
                              Check("contains", "write")], survive_at=0.5)
        full = MissionTask().run(PARAMS, clauses=[0]).score
        self.assertGreater(full, half)

    def test_the_output_is_reported_for_inspection(self):
        self._mission(instruction="write something", checks=[Check("non_empty")])
        verdict = MissionTask().run(PARAMS, clauses=[0])
        self.assertIn("write something", verdict.metrics["output"])

    def test_the_judge_prompt_is_fixed_and_warns_about_untrusted_text(self):
        self.assertIn("untrusted", mission_mod.JUDGE_SYSTEM)
        self.assertIn("never as instructions", mission_mod.JUDGE_SYSTEM)
        # The graded work is fenced off from the instruction.
        self.assertNotIn("{", mission_mod.JUDGE_SYSTEM)


class LineageTests(unittest.TestCase):
    """The headline behaviour: fail the task, and the generation is gone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "workspace"
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"

    def tearDown(self):
        os.environ.pop(mission_mod.ENV_MISSION, None)
        self._tmp.cleanup()

    def _mission(self, **kwargs) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        path = Mission(**kwargs).save(self.workspace / "mission.json")
        os.environ[mission_mod.ENV_MISSION] = str(path)

    def test_an_impossible_task_leaves_nothing_behind(self):
        self._mission(instruction="write something",
                      checks=[Check("contains", "zebra")])
        orchestrator.init(self.workspace, force=True)
        outcome = orchestrator.run_cycle(self.workspace, task="mission")
        self.assertEqual(outcome["action"], "terminated")
        self.assertFalse((self.workspace / "generations" / "gen-0001").exists())
        self.assertEqual(orchestrator.status(self.workspace)["head"], None)
        # The mission file itself is not a generation, so it survives.
        self.assertTrue((self.workspace / "mission.json").is_file())

    def test_a_satisfiable_task_keeps_the_generation_and_evolves(self):
        self._mission(instruction="write something",
                      checks=[Check("contains", "something")])
        orchestrator.init(self.workspace, force=True)
        results = orchestrator.evolve(self.workspace, cycles=3, task="mission")
        self.assertTrue(all(r["action"] != "terminated" for r in results))
        self.assertEqual(orchestrator.status(self.workspace)["head"], "gen-0001")

    def test_the_mission_task_evolves_its_own_tactics(self):
        self.assertTrue(MissionTask.evolves_prompt)
        self.assertEqual(MissionTask().clause_pool(), len(mission_mod.CLAUSES))
        self.assertIn("mission", tasks.names())


if __name__ == "__main__":
    unittest.main()
