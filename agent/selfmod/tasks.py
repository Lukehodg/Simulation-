"""Tasks the agent is judged on.

A task turns a genome into a :class:`Verdict`. The verdict is the whole basis
of the lifecycle: ``passed`` decides whether the generation upgrades or
deletes itself, and ``score`` decides whether a proposed child is an
improvement worth keeping.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field


@dataclass
class Verdict:
    passed: bool
    score: float
    detail: str = ""
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": round(self.score, 6),
            "detail": self.detail,
            "metrics": self.metrics,
        }


class Task:
    name = "task"
    description = ""

    #: Genome parameters this task actually reads. Mutation is restricted to
    #: them, so a lineage never spends a cycle tuning a knob that does nothing.
    #: Empty means "every parameter".
    genes: tuple[str, ...] = ()

    #: Whether the task's prompt clauses are part of what evolves.
    evolves_prompt = False

    def run(self, params: dict, *, seed: int = 0,
            clauses=()) -> Verdict:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------
# Pathfinding under a budget: the default proving ground.
#
# Each instance is a solvable grid. The agent searches it with weighted A*,
# but its genome caps how many nodes it may expand and how wide a frontier it
# may hold, so a badly tuned genome simply runs out of room. Tuning is
# therefore worth something measurable, which is what makes the upgrade loop
# more than decoration.
# --------------------------------------------------------------------------

INSTANCES = 16
PASS_MARK = 0.5  # fraction of instances that must be solved to survive


def _neighbours(x: int, y: int, size: int):
    if x + 1 < size:
        yield x + 1, y
    if y + 1 < size:
        yield x, y + 1
    if x - 1 >= 0:
        yield x - 1, y
    if y - 1 >= 0:
        yield x, y - 1


def open_grid(seed: int, size: int, density: float) -> list[list[int]]:
    """Scattered obstacles: a wide frontier, so the beam cap is what bites."""
    rng = random.Random(seed)
    grid = [[1 if rng.random() < density else 0 for _ in range(size)]
            for _ in range(size)]
    x = y = 0
    grid[0][0] = 0
    while (x, y) != (size - 1, size - 1):  # carve one guaranteed corridor
        if x == size - 1:
            y += 1
        elif y == size - 1:
            x += 1
        elif rng.random() < 0.5:
            x += 1
        else:
            y += 1
        grid[y][x] = 0
    return grid


def perfect_maze(seed: int, size: int, loops: float = 0.05) -> list[list[int]]:
    """Corridors and dead ends: a narrow frontier, so the budget is what bites."""
    rng = random.Random(seed)
    grid = [[1] * size for _ in range(size)]
    grid[0][0] = 0
    seen = {(0, 0)}
    stack = [(0, 0)]
    while stack:
        x, y = stack[-1]
        options = [
            (x + dx, y + dy)
            for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2))
            if 0 <= x + dx < size and 0 <= y + dy < size
            and (x + dx, y + dy) not in seen
        ]
        if not options:
            stack.pop()
            continue
        nx, ny = rng.choice(options)
        grid[(y + ny) // 2][(x + nx) // 2] = 0
        grid[ny][nx] = 0
        seen.add((nx, ny))
        stack.append((nx, ny))
    for _ in range(int(size * size * loops)):  # a few loops, so it is not a tree
        grid[rng.randrange(1, size - 1)][rng.randrange(1, size - 1)] = 0
    grid[size - 1][size - 1] = 0
    grid[size - 2][size - 1] = 0
    grid[size - 1][size - 2] = 0
    return grid


MAZE_SIZES = (21, 25, 29, 33, 37, 41, 45, 49)


def build_instance(index: int, seed: int) -> list[list[int]]:
    """Instance ``index`` of the suite: alternating kind, rising difficulty."""
    if index % 2 == 0:
        return open_grid(seed + index, 25, 0.22 + 0.02 * (index // 2))
    return perfect_maze(seed + index, MAZE_SIZES[index // 2])


def search(grid, *, beam_width: int, heuristic_weight: float, step_budget: int,
           jitter: float, rng: random.Random) -> tuple[bool, int]:
    """Weighted A* with a capped frontier. Returns (solved, expansions)."""
    size = len(grid)
    goal = (size - 1, size - 1)
    frontier_cap = max(4, int(beam_width) * 2)

    def h(node) -> float:
        return abs(node[0] - goal[0]) + abs(node[1] - goal[1])

    start = (0, 0)
    open_heap = [(heuristic_weight * h(start), 0.0, start)]
    best_g = {start: 0.0}
    expansions = 0

    while open_heap and expansions < step_budget:
        _, g, node = heapq.heappop(open_heap)
        if node == goal:
            return True, expansions
        if g > best_g.get(node, float("inf")):
            continue
        expansions += 1
        for nx, ny in _neighbours(node[0], node[1], size):
            if grid[ny][nx]:
                continue
            ng = g + 1.0
            if ng >= best_g.get((nx, ny), float("inf")):
                continue
            best_g[(nx, ny)] = ng
            noise = rng.random() * jitter
            heapq.heappush(
                open_heap,
                (ng + heuristic_weight * h((nx, ny)) + noise, ng, (nx, ny)),
            )
        if len(open_heap) > frontier_cap:
            # Beam: keep only the most promising nodes, and accept that a thin
            # beam can prune the corridor that actually reaches the goal.
            open_heap = heapq.nsmallest(frontier_cap, open_heap)
            heapq.heapify(open_heap)

    return False, expansions


class PathfindTask(Task):
    name = "pathfind"
    description = "solve seeded grid mazes within the genome's search budget"
    genes = ("beam_width", "heuristic_weight", "step_budget", "retry_limit",
             "tie_breaker")

    def run(self, params: dict, *, seed: int = 0, clauses=()) -> Verdict:
        beam_width = int(params.get("beam_width", 3))
        weight = float(params.get("heuristic_weight", 1.0))
        budget = int(params.get("step_budget", 150))
        retries = int(params.get("retry_limit", 0))
        jitter = float(params.get("tie_breaker", 0.0))

        solved = 0
        spent = 0
        for index in range(INSTANCES):
            grid = build_instance(index, seed * 100)
            attempt_total = 0
            ok = False
            for attempt in range(retries + 1):
                rng = random.Random(seed * 7919 + index * 31 + attempt)
                ok, used = search(
                    grid,
                    beam_width=beam_width,
                    heuristic_weight=weight,
                    step_budget=budget,
                    jitter=jitter,
                    rng=rng,
                )
                attempt_total += used
                if ok:
                    break
            solved += 1 if ok else 0
            spent += attempt_total

        rate = solved / INSTANCES
        avg = spent / INSTANCES
        # Solving is what counts; the effort term is a light tie-breaker that
        # stops the lineage from simply inflating its budget forever.
        score = rate - 0.05 * min(1.0, avg / 800.0)
        return Verdict(
            passed=rate >= PASS_MARK,
            score=score,
            detail=f"solved {solved}/{INSTANCES}, {avg:.0f} expansions/instance",
            metrics={"solved": solved, "instances": INSTANCES,
                     "solve_rate": round(rate, 4), "avg_expansions": round(avg, 1)},
        )


class ImpossibleTask(Task):
    """Always fails — the shortest way to watch a generation delete itself."""

    name = "impossible"
    description = "a task no genome can pass; used to demonstrate self-deletion"

    def run(self, params: dict, *, seed: int = 0, clauses=()) -> Verdict:
        return Verdict(False, 0.0, "task is unpassable by construction",
                       {"unreachable": True})


class AlwaysTask(Task):
    """Always passes, with a score that rises as the genome grows its beam."""

    name = "always"
    description = "a trivially passable task; used in tests"

    def run(self, params: dict, *, seed: int = 0, clauses=()) -> Verdict:
        score = min(1.0, float(params.get("beam_width", 1)) / 16.0)
        return Verdict(True, score, "trivially satisfied", {})


REGISTRY: dict[str, Task] = {
    task.name: task for task in (PathfindTask(), ImpossibleTask(), AlwaysTask())
}


def _load_llm() -> None:
    from . import llm_task

    llm_task.register(REGISTRY)


#: Tasks registered on first use, so importing this module never drags in a
#: network client or an optional dependency.
LAZY = {"llm": _load_llm}


def names() -> list[str]:
    return sorted(set(REGISTRY) | set(LAZY))


def get(name: str) -> Task:
    if name not in REGISTRY and name in LAZY:
        LAZY[name]()
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown task {name!r}; known tasks: {', '.join(names())}"
        ) from None
