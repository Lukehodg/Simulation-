"""`earn` mode: pay for yourself or be deleted."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from selfmod import economy, orchestrator, tasks
from selfmod.economy import EarnTask, Plan, SpendLedger, cost_of, read_revenue
from selfmod.errors import TaskUnavailable
from selfmod.mission import Check

PARAMS = {"llm_effort": 1, "llm_answer_tokens": 1024}


class PricingTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(economy.ENV_PRICE_IN, None)
        os.environ.pop(economy.ENV_PRICE_OUT, None)

    def test_a_known_model_is_priced_per_million_tokens(self):
        # Opus 5: $5 in, $25 out per million.
        self.assertAlmostEqual(cost_of("claude-opus-5", 1_000_000, 0), 5.0)
        self.assertAlmostEqual(cost_of("claude-opus-5", 0, 1_000_000), 25.0)
        self.assertAlmostEqual(cost_of("claude-opus-5", 1000, 2000), 0.055)

    def test_the_stand_in_costs_nothing_unless_priced(self):
        self.assertEqual(cost_of("simulated", 5000, 5000), 0.0)
        os.environ[economy.ENV_PRICE_IN] = "5"
        os.environ[economy.ENV_PRICE_OUT] = "25"
        self.assertGreater(cost_of("simulated", 5000, 5000), 0.0)

    def test_an_unknown_model_falls_back_rather_than_costing_nothing(self):
        self.assertGreater(cost_of("some-new-model", 1_000_000, 0), 0.0)


class SpendLedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_costs_accumulate_across_entries(self):
        ledger = SpendLedger(self.workspace)
        ledger.record(generation="gen-0001", model="claude-opus-5",
                      input_tokens=1000, output_tokens=1000, calls=1)
        ledger.record(generation="gen-0002", model="claude-opus-5",
                      input_tokens=1000, output_tokens=1000, calls=1)
        self.assertAlmostEqual(ledger.total(), 0.06)

    def test_the_ledger_sits_above_the_generations(self):
        ledger = SpendLedger(self.workspace)
        ledger.record(generation="gen-0001", model="claude-opus-5",
                      input_tokens=10, output_tokens=10, calls=1)
        self.assertEqual(ledger.path.parent, self.workspace)
        self.assertNotIn("generations", str(ledger.path))

    def test_a_corrupt_line_does_not_lose_the_rest(self):
        ledger = SpendLedger(self.workspace)
        ledger.record(generation="g", model="claude-opus-5",
                      input_tokens=1_000_000, output_tokens=0, calls=1)
        with ledger.path.open("a") as handle:
            handle.write("not json\n")
        self.assertAlmostEqual(ledger.total(), 5.0)


class RevenueTests(unittest.TestCase):
    def test_reads_a_plain_number(self):
        self.assertEqual(read_revenue("echo 42"), 42.0)

    def test_ignores_currency_symbols_and_separators(self):
        self.assertEqual(read_revenue("echo '$1,284.25'"), 1284.25)

    def test_no_command_means_no_revenue(self):
        self.assertEqual(read_revenue(""), 0.0)

    def test_a_failing_command_abstains_rather_than_reporting_zero(self):
        # Being unable to read the balance is not the same as having no money.
        with self.assertRaises(TaskUnavailable):
            read_revenue("exit 3")

    def test_output_without_a_number_abstains(self):
        with self.assertRaises(TaskUnavailable):
            read_revenue("echo nothing here")


class PlanTests(unittest.TestCase):
    def test_a_plan_survives_a_round_trip(self):
        plan = Plan("write a page", stake=2.5, revenue_command="echo 1",
                    checks=[Check("min_words", "50")])
        with tempfile.TemporaryDirectory() as tmp:
            path = plan.save(Path(tmp) / "plan.json")
            data = json.loads(path.read_text())
        self.assertEqual(Plan.from_dict(data).to_dict(), plan.to_dict())

    def test_describe_states_the_survival_rule(self):
        text = Plan("write a page", stake=5).describe()
        self.assertIn("stake + revenue - what it has spent > 0", text)
        self.assertIn("deletes itself", text)
        self.assertIn("none configured", text)

    def test_the_honesty_rules_are_not_part_of_what_evolves(self):
        self.assertIn("original and truthful", economy.BASE_SYSTEM)
        self.assertIn("do not invent credentials", economy.BASE_SYSTEM.lower())
        for clause in economy.CLAUSES:
            self.assertNotIn("truthful", clause)  # the pool cannot repeal it
        self.assertTrue(economy.build_system([0]).startswith(economy.BASE_SYSTEM))


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir(parents=True)
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"
        os.environ["SELFMOD_WORKSPACE"] = str(self.workspace)
        os.environ["SELFMOD_GENERATION"] = "gen-0001"
        os.environ.pop("SELFMOD_LLM_CACHE", None)
        os.environ[economy.ENV_PRICE_IN] = "5"
        os.environ[economy.ENV_PRICE_OUT] = "25"

    def tearDown(self):
        for key in (economy.ENV_PLAN, economy.ENV_PRICE_IN,
                    economy.ENV_PRICE_OUT, "SELFMOD_WORKSPACE",
                    "SELFMOD_GENERATION"):
            os.environ.pop(key, None)
        self._tmp.cleanup()

    def _plan(self, **kwargs) -> Plan:
        plan = Plan(**kwargs)
        os.environ[economy.ENV_PLAN] = str(plan.save(self.workspace / "plan.json"))
        return plan

    def test_no_plan_means_abstain(self):
        os.environ.pop(economy.ENV_PLAN, None)
        with self.assertRaises(TaskUnavailable):
            EarnTask().run(PARAMS, clauses=[0])

    def test_a_funded_generation_survives(self):
        self._plan(brief="write a short advert", stake=1.0)
        verdict = EarnTask().run(PARAMS, clauses=[0])
        self.assertTrue(verdict.passed)
        self.assertTrue(verdict.metrics["solvent"])
        self.assertGreater(verdict.metrics["spent"], 0.0)

    def test_running_out_of_money_fails_the_generation(self):
        self._plan(brief="write a short advert", stake=0.0001)
        verdict = EarnTask().run(PARAMS, clauses=[0])
        self.assertFalse(verdict.passed)
        self.assertIn("BANKRUPT", verdict.detail)

    def test_revenue_keeps_it_alive(self):
        self._plan(brief="write a short advert", stake=0.0001,
                   revenue_command="echo 10")
        verdict = EarnTask().run(PARAMS, clauses=[0])
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.metrics["revenue"], 10.0)

    def test_failing_the_quality_bar_fails_even_when_solvent(self):
        self._plan(brief="write a short advert", stake=100.0,
                   checks=[Check("contains", "zebra")])
        verdict = EarnTask().run(PARAMS, clauses=[0])
        self.assertFalse(verdict.passed)
        self.assertTrue(verdict.metrics["solvent"])

    def test_a_cheaper_cycle_scores_better_at_equal_quality(self):
        self._plan(brief="write a short advert", stake=100.0)
        cheap = EarnTask().run({**PARAMS, "llm_effort": 0}, clauses=[2])
        dear = EarnTask().run({**PARAMS, "llm_effort": 4}, clauses=[7])
        self.assertEqual(cheap.metrics["quality"], dear.metrics["quality"])
        self.assertGreater(cheap.score, dear.score)

    def test_the_work_is_written_out_for_a_human_to_sell(self):
        out = Path(self._tmp.name) / "deliverables"
        self._plan(brief="write a short advert", stake=1.0,
                   deliverables=str(out))
        EarnTask().run(PARAMS, clauses=[0])
        self.assertTrue((out / "gen-0001.txt").is_file())
        self.assertIn("advert", (out / "gen-0001.txt").read_text())

    def test_spending_is_recorded_even_when_the_generation_fails(self):
        self._plan(brief="write a short advert", stake=0.0001)
        EarnTask().run(PARAMS, clauses=[0])
        self.assertGreater(SpendLedger(self.workspace).total(), 0.0)


class LineageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir(parents=True)
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"
        os.environ[economy.ENV_PRICE_IN] = "5"
        os.environ[economy.ENV_PRICE_OUT] = "25"

    def tearDown(self):
        for key in (economy.ENV_PLAN, economy.ENV_PRICE_IN,
                    economy.ENV_PRICE_OUT):
            os.environ.pop(key, None)
        self._tmp.cleanup()

    def test_bankruptcy_deletes_the_generation_but_not_the_books(self):
        plan = Plan(brief="write a short advert", stake=0.0001)
        os.environ[economy.ENV_PLAN] = str(plan.save(self.workspace / "plan.json"))
        orchestrator.init(self.workspace, force=True)
        outcome = orchestrator.run_cycle(self.workspace, task="earn")
        self.assertEqual(outcome["action"], "terminated")
        self.assertFalse((self.workspace / "generations" / "gen-0001").exists())
        self.assertGreater(SpendLedger(self.workspace).total(), 0.0)
        self.assertTrue((self.workspace / "plan.json").is_file())

    def test_a_funded_lineage_keeps_going(self):
        plan = Plan(brief="write a short advert", stake=50.0)
        os.environ[economy.ENV_PLAN] = str(plan.save(self.workspace / "plan.json"))
        orchestrator.init(self.workspace, force=True)
        results = orchestrator.evolve(self.workspace, cycles=3, task="earn")
        self.assertTrue(all(r["action"] != "terminated" for r in results))
        self.assertIn("earn", tasks.names())


if __name__ == "__main__":
    unittest.main()
