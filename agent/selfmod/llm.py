"""Talking to Claude, with the guardrails a self-deleting agent needs.

Three things matter here beyond making the call:

* **Abstention.** Any infrastructure problem raises :class:`TaskUnavailable`
  rather than returning a failing verdict, because a failing verdict deletes
  a generation.
* **Cache.** Replies are cached on disk by the exact request that produced
  them. A parent re-proves itself on every cycle with an unchanged genome, so
  without a cache the lineage would pay for the same answers over and over.
* **Budget.** A hard ceiling on calls per process, because this loop runs
  itself unattended.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import TaskUnavailable

DEFAULT_MODEL = "claude-opus-5"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: Refusal fallbacks: on a policy decline the API re-runs the request on a
#: fallback model inside the same call, so one declined item does not read as
#: a task failure. Scalar "default" form — it pairs with this beta only.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ENV_BACKEND = "SELFMOD_LLM_BACKEND"
ENV_MODEL = "SELFMOD_LLM_MODEL"
ENV_CACHE = "SELFMOD_LLM_CACHE"
ENV_BUDGET = "SELFMOD_LLM_MAX_CALLS"

DEFAULT_BUDGET = 120


@dataclass
class Reply:
    text: str
    output_tokens: int = 0
    refused: bool = False
    cached: bool = False
    input_tokens: int = 0


class Budget:
    """A per-process ceiling on how many billable calls may be made."""

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0

    def charge(self) -> None:
        if self.spent >= self.limit:
            raise TaskUnavailable(
                f"call budget of {self.limit} exhausted; raise {ENV_BUDGET} to continue"
            )
        self.spent += 1


class Cache:
    """Content-addressed reply cache, keyed by the whole request."""

    def __init__(self, directory: Path | None):
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(**request) -> str:
        blob = json.dumps(request, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:32]

    def get(self, key: str) -> Reply | None:
        if not self.directory:
            return None
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return Reply(payload.get("text", ""), payload.get("output_tokens", 0),
                     payload.get("refused", False), cached=True,
                     input_tokens=payload.get("input_tokens", 0))

    def put(self, key: str, reply: Reply) -> None:
        if not self.directory:
            return
        payload = {"text": reply.text, "output_tokens": reply.output_tokens,
                   "refused": reply.refused, "input_tokens": reply.input_tokens}
        tmp = self.directory / f"{key}.json.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.directory / f"{key}.json")


class Backend:
    name = "backend"

    def complete(self, *, system: str, user: str, effort: str,
                 max_tokens: int) -> Reply:  # pragma: no cover
        raise NotImplementedError


class AnthropicBackend(Backend):
    """The real thing: one Messages API call per item."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise TaskUnavailable(
                "the anthropic package is not installed (pip install anthropic)"
            ) from exc
        self.anthropic = anthropic
        self.model = model
        try:
            self.client = anthropic.Anthropic()
        except Exception as exc:  # missing credentials surface here
            raise TaskUnavailable(f"could not construct an Anthropic client: {exc}")

    def complete(self, *, system: str, user: str, effort: str,
                 max_tokens: int) -> Reply:
        anthropic = self.anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                # Thinking is on by default on Opus 5; effort is the depth dial,
                # and it is one of the genes this task evolves.
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise TaskUnavailable(f"authentication failed: {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise TaskUnavailable(f"permission denied: {exc}") from exc
        except anthropic.NotFoundError as exc:
            raise TaskUnavailable(f"unknown model {self.model!r}: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise TaskUnavailable(f"rate limited after SDK retries: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise TaskUnavailable(f"could not reach the API: {exc}") from exc
        except anthropic.BadRequestError as exc:
            # A genome that asks for something the API rejects is the agent's
            # problem, not the environment's: let it score as a failed item.
            return Reply(text="", output_tokens=0, refused=True)
        except anthropic.APIStatusError as exc:
            raise TaskUnavailable(f"API error {exc.status_code}: {exc}") from exc

        if response.stop_reason == "refusal":
            return Reply(text="", output_tokens=0, refused=True)

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return Reply(text=text, output_tokens=response.usage.output_tokens,
                     input_tokens=response.usage.input_tokens)


class Client:
    """A backend wrapped in a cache and a budget."""

    def __init__(self, backend: Backend, *, cache: Cache, budget: Budget,
                 model: str):
        self.backend = backend
        self.cache = cache
        self.budget = budget
        self.model = model
        self.calls = 0
        self.cache_hits = 0
        self.output_tokens = 0
        self.input_tokens = 0

    def ask(self, *, system: str, user: str, effort: str, max_tokens: int) -> Reply:
        key = Cache.key(backend=self.backend.name, model=self.model, system=system,
                        user=user, effort=effort, max_tokens=max_tokens)
        hit = self.cache.get(key)
        if hit is not None:
            # A cached reply is free: it is not charged, and not counted
            # toward spend.
            self.cache_hits += 1
            self.output_tokens += hit.output_tokens
            return hit

        self.budget.charge()
        reply = self.backend.complete(system=system, user=user, effort=effort,
                                      max_tokens=max_tokens)
        self.calls += 1
        self.output_tokens += reply.output_tokens
        self.input_tokens += reply.input_tokens
        self.cache.put(key, reply)
        return reply


def cache_dir() -> Path | None:
    raw = os.environ.get(ENV_CACHE)
    return Path(raw) if raw else None


def build_client(backend: Backend | None = None) -> Client:
    """Assemble the client the LLM task talks through.

    ``SELFMOD_LLM_BACKEND=simulated`` swaps in the offline stand-in, which is
    what the test suite uses: no calls, no key, no spend.
    """
    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL)
    if backend is None:
        choice = os.environ.get(ENV_BACKEND, "anthropic").strip().lower()
        if choice == "simulated":
            from .llm_task import SimulatedBackend

            backend = SimulatedBackend()
        elif choice == "anthropic":
            backend = AnthropicBackend(model)
        else:
            raise TaskUnavailable(f"unknown backend {choice!r}")
    try:
        limit = int(os.environ.get(ENV_BUDGET, DEFAULT_BUDGET))
    except ValueError:
        limit = DEFAULT_BUDGET
    return Client(backend, cache=Cache(cache_dir()), budget=Budget(limit),
                  model=model)
