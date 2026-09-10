import random
import unittest

from selfmod import genome, mutate


class MutateTests(unittest.TestCase):
    def test_children_stay_within_bounds(self):
        rng = random.Random(1)
        params = dict(genome.PARAMS)
        for _ in range(200):
            child, _ = mutate.propose(params, genome.BOUNDS, rng=rng,
                                      integral=genome.INTEGRAL)
            for key, value in child.items():
                low, high = genome.BOUNDS[key]
                self.assertGreaterEqual(value, low)
                self.assertLessEqual(value, high)
                if key in genome.INTEGRAL:
                    self.assertIsInstance(value, int)
            params = child

    def test_a_child_always_differs_from_its_parent(self):
        rng = random.Random(9)
        for _ in range(100):
            child, note = mutate.propose(dict(genome.PARAMS), genome.BOUNDS,
                                         rng=rng, integral=genome.INTEGRAL)
            self.assertNotEqual(child, dict(genome.PARAMS), note)

    def test_pressure_widens_the_search(self):
        calm = [mutate.propose(dict(genome.PARAMS), genome.BOUNDS,
                               rng=random.Random(s), integral=genome.INTEGRAL)[0]
                for s in range(60)]
        pressed = [mutate.propose(dict(genome.PARAMS), genome.BOUNDS,
                                  rng=random.Random(s), integral=genome.INTEGRAL,
                                  pressure=6)[0] for s in range(60)]

        def spread(children):
            base = genome.PARAMS["step_budget"]
            return sum(abs(c["step_budget"] - base) for c in children) / len(children)

        self.assertGreater(spread(pressed), spread(calm))

    def test_seeds_are_stable_across_processes(self):
        # hash() is salted per process; this seed must not be.
        self.assertEqual(mutate.seed_for("gen-0007", 3), 518603457)

    def test_seeds_differ_per_generation_and_cycle(self):
        self.assertNotEqual(mutate.seed_for("gen-0001", 0),
                            mutate.seed_for("gen-0001", 1))
        self.assertNotEqual(mutate.seed_for("gen-0001", 0),
                            mutate.seed_for("gen-0002", 0))


if __name__ == "__main__":
    unittest.main()
