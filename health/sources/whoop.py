"""WHOOP.

The one source here with a real, documented, free API: OAuth 2.0, v2 REST
endpoints, cursor pagination. Recovery, sleep architecture, strain and the
physiological cycle all come from here, and it is first priority for HRV and
resting heart rate because its overnight sampling is the densest we have.

API surface (verify with `health doctor`, which calls it):
    base   https://api.prod.whoop.com/developer
    auth   https://api.prod.whoop.com/oauth/oauth2/auth
    token  https://api.prod.whoop.com/oauth/oauth2/token
"""

from __future__ import annotations

import secrets as pysecrets
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator

import httpx

from .. import metrics as M
from .. import raw as rawstore
from ..models import CycleEvent, Observation, Records, Sleep, Workout
from ..secrets import get_secret, read_tokens, write_tokens
from ..timeutil import isoformat, local_date, parse_ts, sleep_local_date
from .base import Source

API_BASE = "https://api.prod.whoop.com/developer"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = [
    "read:recovery", "read:cycles", "read:sleep", "read:workout",
    "read:profile", "read:body_measurement", "offline",
]

COLLECTIONS = {
    "sleep": "/v2/activity/sleep",
    "recovery": "/v2/recovery",
    "cycle": "/v2/cycle",
    "workout": "/v2/activity/workout",
}
PAGE_LIMIT = 25          # the API's maximum
RATE_LIMIT_PAUSE = 0.65  # 100 requests/minute, with room to spare
KJ_PER_KCAL = 4.184


def _score(record: dict, *path: str, default: Any = None) -> Any:
    """Pull a nested score field. WHOOP omits `score` entirely when a record is
    unscored (still calibrating, or too little data), which is not an error."""
    node: Any = record.get("score")
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return default if node is None else node


def _record_id(record: dict) -> str | None:
    value = record.get("id") or record.get("v1_id") or record.get("cycle_id")
    return str(value) if value is not None else None


def _rmssd_ms(value: float | None) -> float | None:
    """WHOOP's `hrv_rmssd_milli` has been seen in both milliseconds (106.6) and
    seconds (0.1066) depending on endpoint and era. Human RMSSD never sits
    below 1 ms, so a sub-1 value is unambiguously the seconds dialect."""
    if value is None:
        return None
    return value * 1000 if value < 1 else value


class WhoopSource(Source):
    name = "whoop"

    def __init__(self, config, client: httpx.Client | None = None,
                 pause: float = RATE_LIMIT_PAUSE) -> None:
        super().__init__(config)
        self._client = client
        self._pause = pause
        self._tokens: dict | None = None

    # -- auth --------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=API_BASE, timeout=30.0)
        return self._client

    def _client_credentials(self) -> tuple[str, str]:
        return (
            get_secret("WHOOP_CLIENT_ID", self.config.config_dir),
            get_secret("WHOOP_CLIENT_SECRET", self.config.config_dir),
        )

    def authorize(self) -> None:
        """Run the one-time consent flow and store the tokens.

        Your WHOOP developer app must list exactly this redirect URI:
            http://localhost:8765/callback
        """
        client_id, client_secret = self._client_credentials()
        state = pysecrets.token_urlsafe(16)
        params = {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        print(f"Opening WHOOP consent in your browser.\nIf it doesn't open:\n  {url}\n")
        webbrowser.open(url)

        code = _wait_for_callback(state)
        response = httpx.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
        }, timeout=30.0)
        response.raise_for_status()
        self._store_tokens(response.json())
        print("WHOOP authorised. Tokens stored in ~/.config/health/tokens/whoop.json (0600).")

    def _store_tokens(self, payload: dict) -> dict:
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload.get("expires_in", 3600)) - 60
        )
        tokens = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token"),
            "expires_at": isoformat(expires_at),
        }
        write_tokens(self.config.config_dir, self.name, tokens)
        self._tokens = tokens
        return tokens

    def _access_token(self) -> str:
        tokens = self._tokens or read_tokens(self.config.config_dir, self.name)
        if not tokens:
            raise RuntimeError("WHOOP is not authorised yet — run `health auth whoop`.")
        expires_at = parse_ts(tokens.get("expires_at"))
        if expires_at and expires_at > datetime.now(timezone.utc):
            self._tokens = tokens
            return tokens["access_token"]

        client_id, client_secret = self._client_credentials()
        response = httpx.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "offline",
        }, timeout=30.0)
        response.raise_for_status()
        return self._store_tokens(response.json())["access_token"]

    # -- fetch -------------------------------------------------------------

    def _page(self, path: str, params: dict) -> dict:
        response = self.client.get(
            path, params=params,
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 60))
            time.sleep(retry_after)
            return self._page(path, params)
        response.raise_for_status()
        return response.json()

    def iter_pages(self, collection: str, start: datetime,
                   end: datetime | None = None) -> Iterator[dict]:
        path = COLLECTIONS[collection]
        params: dict[str, Any] = {"limit": PAGE_LIMIT, "start": isoformat(start)}
        if end:
            params["end"] = isoformat(end)
        seen_tokens: set[str] = set()
        while True:
            payload = self._page(path, params)
            yield payload
            token = payload.get("next_token") or payload.get("nextToken")
            # A repeated token means the API is looping; stop rather than spin.
            if not token or token in seen_tokens:
                return
            seen_tokens.add(token)
            params["nextToken"] = token
            if self._pause:
                time.sleep(self._pause)

    def fetch(self, since: datetime | None = None,
              until: datetime | None = None) -> list[Path]:
        start = since or datetime.now(timezone.utc) - timedelta(days=30)
        written: list[Path] = []
        for collection in COLLECTIONS:
            seen = 0
            for page in self.iter_pages(collection, start, until):
                records = page.get("records") or []
                seen += len(records)
                self.report_progress(collection, seen)
                if not records:
                    continue
                written.append(rawstore.write(
                    self.config.raw_dir, self.name, collection, page
                ))
        return written

    def check(self) -> tuple[bool, str]:
        try:
            payload = self._page(COLLECTIONS["recovery"], {"limit": 1})
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, str(exc).splitlines()[0]
        n = len(payload.get("records", []))
        return True, f"reachable, {n} recent recovery record(s)"

    # -- parse -------------------------------------------------------------

    def parse(self, path: Path) -> Records:
        payload = rawstore.RawFile(path, self.name, path.parent.parent.name,
                                   datetime.now(timezone.utc)).load()
        kind = path.parent.parent.name
        records = Records()
        for item in payload.get("records", []):
            if kind == "sleep":
                self._parse_sleep(item, records)
            elif kind == "recovery":
                self._parse_recovery(item, records)
            elif kind == "cycle":
                self._parse_cycle(item, records)
            elif kind == "workout":
                self._parse_workout(item, records)
        return records

    def _observe(self, records: Records, ts, day, metric, value, source_id) -> None:
        if value is None:
            return
        records.observations.append(Observation(
            ts=ts, local_date=day, metric=metric, value=float(value),
            unit=M.unit_for(metric), source=self.name, source_id=source_id,
        ))

    def _parse_sleep(self, item: dict, records: Records) -> None:
        sid = _record_id(item)
        start, end = parse_ts(item.get("start")), parse_ts(item.get("end"))
        if not (sid and start and end):
            return
        day = sleep_local_date(end, self.config.timezone)
        stages = _score(item, "stage_summary", default={}) or {}

        def minutes(key: str) -> float | None:
            value = stages.get(key)
            return round(value / 60000, 1) if value is not None else None

        in_bed = minutes("total_in_bed_time_milli")
        awake = minutes("total_awake_time_milli")
        light = minutes("total_light_sleep_time_milli")
        deep = minutes("total_slow_wave_sleep_time_milli")
        rem = minutes("total_rem_sleep_time_milli")
        asleep = None
        if None not in (light, deep, rem):
            asleep = round(light + deep + rem, 1)

        is_nap = bool(item.get("nap", False))
        records.sleeps.append(Sleep(
            source=self.name, source_id=sid, start_ts=start, end_ts=end, local_date=day,
            duration_min=asleep, in_bed_min=in_bed,
            efficiency=_score(item, "sleep_efficiency_percentage"),
            rem_min=rem, deep_min=deep, light_min=light, awake_min=awake, is_nap=is_nap,
            respiratory_rate=_score(item, "respiratory_rate"),
        ))
        # Naps must not overwrite the night's numbers in the daily series.
        if not is_nap:
            self._observe(records, end, day, M.SLEEP_DURATION, asleep, sid)
            self._observe(records, end, day, M.SLEEP_EFFICIENCY,
                          _score(item, "sleep_efficiency_percentage"), sid)
            self._observe(records, end, day, M.RESPIRATORY_RATE,
                          _score(item, "respiratory_rate"), sid)

    def _parse_recovery(self, item: dict, records: Records) -> None:
        sid = str(item.get("sleep_id") or item.get("cycle_id") or "")
        ts = parse_ts(item.get("updated_at") or item.get("created_at"))
        if not (sid and ts):
            return
        day = local_date(ts, self.config.timezone)
        if _score(item, "user_calibrating") is True:
            return  # WHOOP says these scores aren't yet meaningful; believe it
        self._observe(records, ts, day, M.RECOVERY_SCORE, _score(item, "recovery_score"), sid)
        self._observe(records, ts, day, M.RESTING_HR, _score(item, "resting_heart_rate"), sid)
        self._observe(records, ts, day, M.HRV_RMSSD,
                      _rmssd_ms(_score(item, "hrv_rmssd_milli")), sid)
        self._observe(records, ts, day, M.SPO2, _score(item, "spo2_percentage"), sid)
        self._observe(records, ts, day, M.SKIN_TEMP_DEV, _score(item, "skin_temp_celsius"), sid)

    def _parse_cycle(self, item: dict, records: Records) -> None:
        sid = _record_id(item)
        start = parse_ts(item.get("start"))
        if not (sid and start):
            return
        day = local_date(start, self.config.timezone)
        self._observe(records, start, day, M.STRAIN, _score(item, "strain"), sid)
        self._observe(records, start, day, M.HR_AVG, _score(item, "average_heart_rate"), sid)
        self._observe(records, start, day, M.HR_MAX, _score(item, "max_heart_rate"), sid)
        kj = _score(item, "kilojoule")
        if kj is not None:
            self._observe(records, start, day, M.ACTIVE_ENERGY, kj / KJ_PER_KCAL, sid)

    def _parse_workout(self, item: dict, records: Records) -> None:
        sid = _record_id(item)
        start, end = parse_ts(item.get("start")), parse_ts(item.get("end"))
        if not (sid and start):
            return
        day = local_date(start, self.config.timezone)
        kj = _score(item, "kilojoule")
        records.workouts.append(Workout(
            source=self.name, source_id=sid, start_ts=start, end_ts=end, local_date=day,
            type=str(item.get("sport_name") or item.get("sport_id") or "") or None,
            duration_min=round((end - start).total_seconds() / 60, 1) if end else None,
            distance_m=_score(item, "distance_meter"),
            avg_hr=_score(item, "average_heart_rate"),
            max_hr=_score(item, "max_heart_rate"),
            kcal=round(kj / KJ_PER_KCAL, 1) if kj is not None else None,
            strain=_score(item, "strain"),
        ))


def _wait_for_callback(expected_state: str, port: int = 8765) -> str:
    """Catch the OAuth redirect on localhost. Blocks until the browser hits it."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in captured and captured.get("state") == expected_state
            body = "<h2>WHOOP connected.</h2><p>You can close this tab.</p>" if ok \
                else "<h2>Authorisation failed.</h2><p>Check the terminal.</p>"
            self.wfile.write(body.encode())

        def log_message(self, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.handle_request()

    if captured.get("state") != expected_state:
        raise RuntimeError("OAuth state mismatch — start the flow again.")
    if "code" not in captured:
        raise RuntimeError(f"WHOOP returned no code: {captured}")
    return captured["code"]
