"""Test package.

The LLM task defaults to calling the real API. Every test in this suite runs
against the deterministic simulated backend instead, set here so that no test
— including the ones that spawn generations as subprocesses — can spend money
or need a key.
"""

import os

os.environ.setdefault("SELFMOD_LLM_BACKEND", "simulated")
