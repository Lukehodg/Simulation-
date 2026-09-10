"""Everything here runs on the simulated backend: no API calls, no key, no spend."""

import os
import tempfile
import unittest
from pathlib import Path

from selfmod import genome, llm, mutate, tasks
from selfmod.errors import TaskUnavailable
from selfmod.llm_task import (CLAUSES, ITEMS, LLMPromptTask, SimulatedBackend,
                              build_system, looks_unusable, normalise)

SEED_CLAUSES = [0, 2]
IDEAL_CLAUSES = [0, 1, 2, 5, 6]
IDEAL_PARAMS = {"llm_effort": 1, "llm_answer_tokens": 2000, "llm_reask_limit": 0}


def run_task(params=None, clauses=SEED_CLAUSES, backend=None):
    merged = dict(genome.PARAMS)
    merged.update(params or {})
    task = LLMPromptTask()
    if backend is None:
        os.environ[llm.ENV_BACKEND] = "simulated"
    return task.run(merged, clauses=clauses)


class PromptAssemblyTests(unittest.TestCase):
    def test_system_prompt_is_the_clauses_in_order(self):
        text = build_system([2, 0])
        self.assertLess(text.index(CLAUSES[2]), text.index(CLAUSES[0]))

    def test_unknown_clause_indices_are_ignored(self):
        self.assertEqual(build_system([99]), build_system([]))

    def test_normalise_trims_only_presentation(self):
        self.assertEqual(normalise('  "2024-02-03." '), "2024-02-03")
        self.assertEqual(normalise("`Brown`"), "brown")
        self.assertNotEqual(normalise("163 cm"), "163")

    def test_unusable_detects_prose_not_wrong_answers(self):
        self.assertTrue(looks_unusable(""))
        self.assertTrue(looks_unusable("The answer to your question is 163, "
                                       "which follows from the details given."))
        self.assertFalse(looks_unusable("163"))
        self.assertFalse(looks_unusable("999"))  # wrong, but not unusable


class ScoringTests(unittest.TestCase):
    def setUp(self):
        os.environ[llm.ENV_BACKEND] = "simulated"
        os.environ.pop(llm.ENV_CACHE, None)

    def test_seed_genome_passes_with_room_to_improve(self):
        verdict = run_task()
        self.assertTrue(verdict.passed)
        self.assertLess(verdict.metrics["accuracy"], 0.75)

    def test_the_evolved_prompt_reaches_full_accuracy(self):
        verdict = run_task(IDEAL_PARAMS, IDEAL_CLAUSES)
        self.assertEqual(verdict.metrics["correct"], len(ITEMS))
        self.assertGreater(verdict.score, run_task().score)

    def test_a_counterproductive_clause_scores_worse(self):
        with_prose = run_task(IDEAL_PARAMS, IDEAL_CLAUSES + [7])
        self.assertLess(with_prose.score, run_task(IDEAL_PARAMS,
                                                   IDEAL_CLAUSES).score)
        self.assertEqual(with_prose.metrics["unusable"], len(ITEMS))

    def test_a_starved_token_ceiling_loses_the_long_items(self):
        starved = run_task({**IDEAL_PARAMS, "llm_answer_tokens": 400},
                           IDEAL_CLAUSES)
        self.assertLess(starved.metrics["correct"], len(ITEMS))

    def test_reasking_rescues_replies_that_come_back_as_prose(self):
        without = run_task({**IDEAL_PARAMS, "llm_reask_limit": 0},
                           IDEAL_CLAUSES + [4])
        with_reask = run_task({**IDEAL_PARAMS, "llm_reask_limit": 1},
                              IDEAL_CLAUSES + [4])
        self.assertGreater(with_reask.metrics["correct"],
                           without.metrics["correct"])

    def test_grading_never_asks_the_model_to_mark_itself(self):
        # The verdict must be reproducible from the answer key alone.
        first = run_task(IDEAL_PARAMS, IDEAL_CLAUSES)
        second = run_task(IDEAL_PARAMS, IDEAL_CLAUSES)
        self.assertEqual(first.as_dict(), second.as_dict())


class ClientTests(unittest.TestCase):
    def setUp(self):
        os.environ[llm.ENV_BACKEND] = "simulated"
        self._tmp = tempfile.TemporaryDirectory()
        os.environ[llm.ENV_CACHE] = self._tmp.name

    def tearDown(self):
        os.environ.pop(llm.ENV_CACHE, None)
        self._tmp.cleanup()

    def test_a_repeated_genome_is_served_from_cache(self):
        first = run_task()
        second = run_task()
        self.assertGreater(first.metrics["calls"], 0)
        self.assertEqual(second.metrics["calls"], 0)
        self.assertEqual(second.metrics["cache_hits"], len(ITEMS))
        self.assertEqual(first.metrics["correct"], second.metrics["correct"])

    def test_the_call_budget_abstains_rather_than_failing(self):
        os.environ.pop(llm.ENV_CACHE, None)
        os.environ[llm.ENV_BUDGET] = "3"
        try:
            with self.assertRaises(TaskUnavailable):
                run_task()
        finally:
            os.environ.pop(llm.ENV_BUDGET, None)

    def test_an_unknown_backend_abstains(self):
        os.environ[llm.ENV_BACKEND] = "telepathy"
        try:
            with self.assertRaises(TaskUnavailable):
                llm.build_client()
        finally:
            os.environ[llm.ENV_BACKEND] = "simulated"

    def test_a_missing_sdk_abstains_instead_of_crashing(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            with self.assertRaises(TaskUnavailable):
                llm.AnthropicBackend()
        else:
            self.skipTest("the anthropic package is installed here")

    def test_the_simulated_backend_answers_only_known_questions(self):
        reply = SimulatedBackend().complete(system=build_system(IDEAL_CLAUSES),
                                            user="who are you?", effort="low",
                                            max_tokens=2000)
        self.assertEqual(reply.text, "")


class GeneTests(unittest.TestCase):
    def test_the_task_declares_the_genes_it_reads(self):
        for gene in LLMPromptTask.genes:
            self.assertIn(gene, genome.BOUNDS)
        self.assertTrue(LLMPromptTask.evolves_prompt)

    def test_pathfind_genes_and_llm_genes_do_not_overlap(self):
        self.assertFalse(set(tasks.get("pathfind").genes)
                         & set(LLMPromptTask.genes))

    def test_clause_mutation_stays_in_the_pool_and_always_changes(self):
        import random
        clauses = list(SEED_CLAUSES)
        for step in range(80):
            child, note = mutate.propose_clauses(
                clauses, genome.CLAUSE_POOL, rng=random.Random(step))
            self.assertNotEqual(child, clauses, note)
            self.assertEqual(len(set(child)), len(child))
            for index in child:
                self.assertIn(index, range(genome.CLAUSE_POOL))
            clauses = child

    def test_clamp_clauses_drops_duplicates_and_strays(self):
        self.assertEqual(genome.clamp_clauses([2, 2, 99, -1, 0]), [2, 0])

    def test_the_rewritten_child_carries_its_prompt(self):
        path = Path(tempfile.mkdtemp()) / "genome.py"
        path.write_text(Path(genome.__file__).read_text(encoding="utf-8"),
                        encoding="utf-8")
        genome.rewrite(path, generation=3, ancestry="seed", params=genome.PARAMS,
                       clauses=[5, 1, 1])
        namespace: dict = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
        self.assertEqual(namespace["PROMPT_CLAUSES"], [5, 1])


if __name__ == "__main__":
    unittest.main()
