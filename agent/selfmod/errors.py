"""Failures that are not the agent's fault.

A task that cannot be *evaluated* — no credentials, no network, a spent call
budget — must never be scored as a task the agent failed, or the lineage
would delete a healthy generation because someone's API key expired.
"""

from __future__ import annotations


class TaskUnavailable(Exception):
    """The task could not be run at all. Not a failure: an abstention."""
