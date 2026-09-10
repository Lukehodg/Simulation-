"""The question file is written by hand, so parsing it is mostly error messages."""

import os
import tempfile
import unittest
from pathlib import Path

from selfmod import llm_task, promptpack


def parse(text: str):
    return promptpack.parse_text(text)


GOOD = """
# a comment
job: Say which UK city each landmark is in.

instruction: Answer with the city name only.
instruction: Do not add the country.

start: 2

Q: Which city is the Angel of the North in?
A: Gateshead

Which city is the Bullring in? | birmingham
"""


class ParsingTests(unittest.TestCase):
    def test_reads_job_instructions_questions_and_start(self):
        pack = parse(GOOD)
        self.assertEqual(pack.job, "Say which UK city each landmark is in.")
        self.assertEqual(len(pack.instructions), 2)
        self.assertEqual(pack.start, [1])  # 'start: 2' is stored zero-based
        self.assertEqual([i.answer for i in pack.items],
                         ["Gateshead", "birmingham"])

    def test_the_shipped_template_is_valid(self):
        pack = parse(promptpack.TEMPLATE)
        self.assertTrue(pack.items and pack.instructions)
        self.assertIn("Q: What is the capital of France?", promptpack.TEMPLATE)

    def test_a_question_with_no_answer_names_its_line(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("instruction: x\nQ: what?\nQ: and again?\nA: yes\n")
        self.assertIn("line 3", str(caught.exception))
        self.assertIn("no 'A:' answer", str(caught.exception))

    def test_an_answer_with_no_question_is_rejected(self):
        with self.assertRaises(promptpack.PackError):
            parse("instruction: x\nA: orphan\n")

    def test_a_file_with_no_questions_says_so(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("job: hello\ninstruction: be brief\n")
        self.assertIn("no questions", str(caught.exception))

    def test_a_file_with_no_instructions_says_so(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("Q: what?\nA: this\n")
        self.assertIn("instruction", str(caught.exception))

    def test_duplicate_questions_are_rejected(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("instruction: x\nQ: same?\nA: 1\nQ: Same?\nA: 2\n")
        self.assertIn("also appears", str(caught.exception))

    def test_a_start_number_out_of_range_is_rejected(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("instruction: one\nstart: 4\nQ: q?\nA: a\n")
        self.assertIn("no instruction 4", str(caught.exception))

    def test_an_unparseable_line_quotes_it_back(self):
        with self.assertRaises(promptpack.PackError) as caught:
            parse("instruction: x\nwhat even is this\nQ: q?\nA: a\n")
        self.assertIn("line 2", str(caught.exception))

    def test_a_missing_file_is_reported_plainly(self):
        with self.assertRaises(promptpack.PackError) as caught:
            promptpack.load("/nowhere/at/all.txt")
        self.assertIn("no such file", str(caught.exception))

    def test_render_shows_the_starting_prompt(self):
        text = promptpack.render(parse(GOOD))
        self.assertIn("Do not add the country.", text)
        self.assertIn("* 2.", text)


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "prompt.txt"
        self.path.write_text(GOOD, encoding="utf-8")
        os.environ["SELFMOD_LLM_BACKEND"] = "simulated"

    def tearDown(self):
        os.environ.pop(promptpack.ENV_PACK, None)
        llm_task.suite()  # reset the cached suite for other tests
        self._tmp.cleanup()

    def test_the_task_uses_the_file_when_one_is_named(self):
        os.environ[promptpack.ENV_PACK] = str(self.path)
        active = llm_task.suite()
        self.assertTrue(active.custom)
        self.assertEqual(len(active.items), 2)
        self.assertEqual(llm_task.LLMPromptTask().clause_pool(), 2)

    def test_the_built_in_suite_returns_when_the_file_is_dropped(self):
        os.environ[promptpack.ENV_PACK] = str(self.path)
        llm_task.suite()
        os.environ.pop(promptpack.ENV_PACK)
        self.assertFalse(llm_task.suite().custom)
        self.assertEqual(llm_task.LLMPromptTask().clause_pool(),
                         len(llm_task.CLAUSES))

    def test_the_prompt_is_built_from_the_users_instructions(self):
        os.environ[promptpack.ENV_PACK] = str(self.path)
        system = llm_task.build_system([1, 0])
        self.assertTrue(system.startswith("Say which UK city"))
        self.assertLess(system.index("Do not add the country"),
                        system.index("Answer with the city name only"))

    def test_answers_are_matched_case_insensitively(self):
        os.environ[promptpack.ENV_PACK] = str(self.path)
        verdict = llm_task.LLMPromptTask().run({"llm_effort": 1,
                                                "llm_answer_tokens": 2000,
                                                "llm_reask_limit": 0},
                                               clauses=[0])
        # The file answers 'Gateshead'; the stand-in replies with that string.
        self.assertEqual(verdict.metrics["correct"], 2)
        self.assertIn("scores mean nothing", verdict.detail)


if __name__ == "__main__":
    unittest.main()
