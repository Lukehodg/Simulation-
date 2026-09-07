"""Hevy.

Set-level strength data — the only source here that records what you actually
lifted, rep by rep. First sync pages the whole history; after that it follows
the events feed, which also reports workouts you edited or deleted after the
fact (a corrected weight, a session logged twice).

API surface:
    base    https://api.hevyapp.com/v1
    auth    api-key header (Hevy Pro only)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

from .. import raw as rawstore
from ..models import ExerciseTemplate, Records, StrengthSet, Workout
from ..secrets import get_secret
from ..timeutil import isoformat, local_date, parse_ts
from .base import Source

API_BASE = "https://api.hevyapp.com/v1"
PAGE_SIZE = 10          # the workouts endpoint caps pageSize at 10
EVENT_PAGE_SIZE = 10
TEMPLATE_PAGE_SIZE = 100
RATE_LIMIT_PAUSE = 0.3
MAX_PAGES = 2000        # a guard against an endless pager, not a real limit


class HevySource(Source):
    name = "hevy"

    def __init__(self, config, client: httpx.Client | None = None,
                 pause: float = RATE_LIMIT_PAUSE) -> None:
        super().__init__(config)
        self._client = client
        self._pause = pause

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=API_BASE, timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"api-key": get_secret("HEVY_API_KEY", self.config.config_dir),
                "Accept": "application/json"}

    def _get(self, path: str, params: dict) -> dict:
        response = self.client.get(path, params=params, headers=self._headers())
        if response.status_code == 429:
            time.sleep(float(response.headers.get("Retry-After", 30)))
            return self._get(path, params)
        response.raise_for_status()
        return response.json()

    def iter_pages(self, path: str, params: dict) -> Iterator[dict]:
        page = 1
        while page <= MAX_PAGES:
            payload = self._get(path, {**params, "page": page,
                                       "pageSize": params.get("pageSize", PAGE_SIZE)})
            yield payload
            page_count = payload.get("page_count") or payload.get("pageCount") or 1
            if page >= int(page_count):
                return
            page += 1
            if self._pause:
                time.sleep(self._pause)

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        """With `since`, follow the events feed; without it, page everything."""
        written: list[Path] = []
        if since:
            pages = self.iter_pages("/workouts/events",
                                    {"since": isoformat(since), "pageSize": EVENT_PAGE_SIZE})
            kind = "events"
        else:
            pages = self.iter_pages("/workouts", {"pageSize": PAGE_SIZE})
            kind = "workouts"
        for payload in pages:
            if not (payload.get("workouts") or payload.get("events")):
                continue
            written.append(rawstore.write(self.config.raw_dir, self.name, kind, payload))

        # The catalogue is small and changes whenever you add a custom
        # exercise, so it is cheap to refresh every sync and annoying to have
        # stale: an unmapped exercise drops out of volume-by-muscle.
        written.extend(self._fetch_templates())
        return written

    def _fetch_templates(self) -> list[Path]:
        written: list[Path] = []
        for payload in self.iter_pages("/exercise_templates",
                                       {"pageSize": TEMPLATE_PAGE_SIZE}):
            items = payload.get("exercise_templates") or payload.get("templates")
            if not items:
                continue
            written.append(rawstore.write(self.config.raw_dir, self.name,
                                          "exercise_templates", payload))
        return written

    def check(self) -> tuple[bool, str]:
        try:
            payload = self._get("/workouts/count", {})
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, str(exc).splitlines()[0]
        count = payload.get("workout_count", payload.get("count"))
        return True, f"reachable, {count} workouts on the account"

    # -- parse -------------------------------------------------------------

    def parse(self, path: Path) -> Records:
        payload: Any = rawstore.RawFile(path, self.name, path.parent.parent.name,
                                        datetime.now(timezone.utc)).load()
        records = Records()
        for workout in payload.get("workouts", []) or []:
            self._parse_workout(workout, records)
        for template in (payload.get("exercise_templates")
                         or payload.get("templates") or []):
            self._parse_template(template, records)
        for event in payload.get("events", []) or []:
            kind = (event.get("type") or "").lower()
            if kind == "deleted":
                deleted_id = event.get("id") or (event.get("workout") or {}).get("id")
                if deleted_id:
                    records.deleted_workouts.append(str(deleted_id))
            elif event.get("workout"):
                self._parse_workout(event["workout"], records)
        return records

    @staticmethod
    def _muscles(value: object) -> str | None:
        """Hevy sends secondary muscles as a list; store them comma-joined so a
        LIKE query still works without a second table."""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) or None
        return str(value) if value else None

    def _parse_template(self, template: dict, records: Records) -> None:
        template_id = template.get("id") or template.get("exercise_template_id")
        if not template_id:
            return
        records.exercise_templates.append(ExerciseTemplate(
            source=self.name, template_id=str(template_id),
            title=template.get("title") or template.get("name"),
            primary_muscle=template.get("primary_muscle_group"),
            secondary_muscles=self._muscles(template.get("secondary_muscle_groups")),
            equipment=template.get("equipment"),
            is_custom=template.get("is_custom"),
        ))

    def _parse_workout(self, workout: dict, records: Records) -> None:
        wid = workout.get("id")
        start = parse_ts(workout.get("start_time") or workout.get("startTime"))
        if not (wid and start):
            return
        wid = str(wid)
        end = parse_ts(workout.get("end_time") or workout.get("endTime"))
        day = local_date(start, self.config.timezone)

        records.workouts.append(Workout(
            source=self.name, source_id=wid, start_ts=start, end_ts=end, local_date=day,
            type="strength", title=workout.get("title"),
            duration_min=round((end - start).total_seconds() / 60, 1) if end else None,
        ))

        for exercise_idx, exercise in enumerate(workout.get("exercises", []) or []):
            title = exercise.get("title") or exercise.get("name") or "unknown"
            template_id = exercise.get("exercise_template_id") or exercise.get("templateId")
            for set_idx, item in enumerate(exercise.get("sets", []) or []):
                records.strength_sets.append(StrengthSet(
                    source=self.name, workout_id=wid,
                    exercise_idx=exercise.get("index", exercise_idx),
                    set_idx=item.get("index", set_idx),
                    ts=start, local_date=day,
                    exercise=title, exercise_id=template_id,
                    set_type=item.get("type"),
                    weight_kg=item.get("weight_kg", item.get("weightKg")),
                    reps=item.get("reps"),
                    distance_m=item.get("distance_meters", item.get("distanceMeters")),
                    duration_s=item.get("duration_seconds", item.get("durationSeconds")),
                    rpe=item.get("rpe"),
                ))
