import unittest

from selfmod import genome, tasks


class TaskTests(unittest.TestCase):
    def test_every_instance_is_solvable_by_exhaustive_search(self):
        generous = {"beam_width": 64, "heuristic_weight": 1.0,
                    "step_budget": 100000, "retry_limit": 0, "tie_breaker": 0.0}
        verdict = tasks.get("pathfind").run(generous, seed=3)
        self.assertEqual(verdict.metrics["solved"], tasks.INSTANCES)

    def test_the_suite_is_deterministic_for_a_seed(self):
        first = tasks.get("pathfind").run(dict(genome.PARAMS), seed=5)
        second = tasks.get("pathfind").run(dict(genome.PARAMS), seed=5)
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.metrics, second.metrics)

    def test_the_seed_genome_passes_but_leaves_room(self):
        verdict = tasks.get("pathfind").run(dict(genome.PARAMS), seed=7)
        self.assertTrue(verdict.passed)
        self.assertLess(verdict.score, 0.9)

    def test_a_starved_genome_fails(self):
        starved = {"beam_width": 1, "heuristic_weight": 1.0, "step_budget": 40,
                   "retry_limit": 0, "tie_breaker": 0.0}
        self.assertFalse(tasks.get("pathfind").run(starved, seed=7).passed)

    def test_a_tuned_genome_scores_better_than_the_seed(self):
        tuned = {"beam_width": 12, "heuristic_weight": 1.5, "step_budget": 700,
                 "retry_limit": 2, "tie_breaker": 0.3}
        seed_score = tasks.get("pathfind").run(dict(genome.PARAMS), seed=7).score
        self.assertGreater(tasks.get("pathfind").run(tuned, seed=7).score,
                           seed_score)

    def test_impossible_task_never_passes(self):
        self.assertFalse(tasks.get("impossible").run(dict(genome.PARAMS)).passed)

    def test_unknown_task_names_are_reported(self):
        with self.assertRaises(KeyError):
            tasks.get("no-such-task")


if __name__ == "__main__":
    unittest.main()
