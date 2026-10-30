"""Load and parse Intervals.icu cycling workouts for an ERG trainer."""

from __future__ import annotations

import base64
import datetime as dt
import getpass
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_BASE = "https://intervals.icu/api/v1"
KEYCHAIN_SERVICE = "codex.intervals.icu"
KEYCHAIN_ACCOUNT = "api-key"


class WorkoutError(RuntimeError):
    pass


def is_affirmative_response(value: str) -> bool:
    """Accept yes even when a prior macOS function key left a CSI sequence in stdin."""
    without_terminal_keys = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return without_terminal_keys.strip().lower() in {"y", "yes", "j", "ja"}


@dataclass(frozen=True)
class WorkoutStep:
    name: str
    duration_seconds: int
    mode: str  # steady, ramp, freeride
    start_value: float | None = None
    end_value: float | None = None
    value_unit: str | None = None  # percent or watts
    cadence_min: int | None = None
    cadence_max: int | None = None


@dataclass(frozen=True)
class Workout:
    name: str
    event_id: str
    start_date_local: str
    description: str
    steps: tuple[WorkoutStep, ...]

    @property
    def duration_seconds(self) -> int:
        return sum(step.duration_seconds for step in self.steps)


_DURATION_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)(h|min|m|s)(?!\w)", re.I)
_RANGE_PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%", re.I)
_PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)%", re.I)
_RANGE_WATTS_RE = re.compile(r"(?<![\d.])(\d+)\s*-\s*(\d+)\s*w(?:atts?)?\b", re.I)
_WATTS_RE = re.compile(r"(?<![\d.])(\d+)\s*w(?:atts?)?\b", re.I)
_CADENCE_RANGE_RE = re.compile(r"(?<!\d)(\d+)\s*-\s*(\d+)\s*rpm\b", re.I)
_CADENCE_RE = re.compile(r"(?<!\d)(\d+)\s*rpm\b", re.I)
_REPEAT_RE = re.compile(r"(?:^|\s)(\d+)\s*x\s*$", re.I)


def parse_duration(token: re.Match[str]) -> int:
    value = float(token.group(1))
    unit = token.group(2).lower()
    multiplier = 3600 if unit == "h" else 60 if unit in {"m", "min"} else 1
    seconds = int(round(value * multiplier))
    if seconds <= 0:
        raise WorkoutError("Step duration must be positive")
    return seconds


def _parse_cadence(text: str) -> tuple[int | None, int | None]:
    match = _CADENCE_RANGE_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _CADENCE_RE.search(text)
    if match:
        value = int(match.group(1))
        return value, value
    return None, None


def parse_step(line: str) -> WorkoutStep:
    text = line.strip()
    if text.startswith("-"):
        text = text[1:].strip()
    duration_matches = list(_DURATION_RE.finditer(text))
    if not duration_matches:
        raise WorkoutError(f"No time duration found in step: {line}")
    # The step name may itself contain a duration (for example "20min-Test").
    # The actual prescription duration is the last duration token before targets.
    duration_match = duration_matches[-1]
    duration = parse_duration(duration_match)
    name = text[: duration_match.start()].strip(" :-") or "Workout step"
    cadence_min, cadence_max = _parse_cadence(text)
    after_duration = text[duration_match.end() :]

    if re.search(r"\bfreeride\b", after_duration, re.I):
        return WorkoutStep(name, duration, "freeride", cadence_min=cadence_min, cadence_max=cadence_max)

    is_ramp = bool(re.search(r"\bramp\b", after_duration, re.I))
    match = _RANGE_PERCENT_RE.search(after_duration)
    if match:
        start, end = float(match.group(1)), float(match.group(2))
        if not is_ramp:
            start = end = (start + end) / 2
        return WorkoutStep(name, duration, "ramp" if is_ramp else "steady", start, end, "percent", cadence_min, cadence_max)
    match = _RANGE_WATTS_RE.search(after_duration)
    if match:
        start, end = float(match.group(1)), float(match.group(2))
        if not is_ramp:
            start = end = (start + end) / 2
        return WorkoutStep(name, duration, "ramp" if is_ramp else "steady", start, end, "watts", cadence_min, cadence_max)
    match = _PERCENT_RE.search(after_duration)
    if match:
        value = float(match.group(1))
        return WorkoutStep(name, duration, "steady", value, value, "percent", cadence_min, cadence_max)
    match = _WATTS_RE.search(after_duration)
    if match:
        value = float(match.group(1))
        return WorkoutStep(name, duration, "steady", value, value, "watts", cadence_min, cadence_max)

    if re.search(r"\b(?:Z[1-7]|HR|pace|km|mi|mtr)\b", after_duration, re.I):
        raise WorkoutError(
            f"Step cannot be converted safely to ERG power: {line}. "
            "Use a watt target, FTP percentage, or freeride."
        )
    raise WorkoutError(f"No ERG power target found in step: {line}")


def parse_workout_description(
    description: str,
    *,
    name: str = "Intervals.icu workout",
    event_id: str = "",
    start_date_local: str = "",
) -> Workout:
    groups: list[tuple[int, list[WorkoutStep]]] = []
    repeat = 1
    group: list[WorkoutStep] = []

    def flush() -> None:
        nonlocal group
        if group:
            groups.append((repeat, group))
            group = []

    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("-"):
            group.append(parse_step(line))
            continue
        flush()
        match = _REPEAT_RE.search(line)
        repeat = int(match.group(1)) if match else 1
        if repeat < 1 or repeat > 100:
            raise WorkoutError(f"Invalid repeat count in section: {line}")
    flush()

    steps: list[WorkoutStep] = []
    for count, section_steps in groups:
        for _ in range(count):
            steps.extend(section_steps)
    if not steps:
        raise WorkoutError("Workout contains no structured steps")
    return Workout(name, event_id, start_date_local, description, tuple(steps))


def keychain_get() -> str | None:
    if sys.platform != "darwin":
        return None
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def get_api_key() -> str:
    key = os.environ.get("INTERVALS_ICU_API_KEY") or keychain_get()
    if not key:
        raise WorkoutError(
            "No Intervals.icu API key found. Configure it with the AI_Coach setup-key command."
        )
    return key


def api_request(path: str) -> Any:
    token = base64.b64encode(f"API_KEY:{get_api_key()}".encode()).decode("ascii")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json", "User-Agent": "trainer-intervals-erg/1.0"},
    )
    verify_paths = ssl.get_default_verify_paths()
    system_ca = Path("/etc/ssl/cert.pem")
    context = (
        ssl.create_default_context(cafile=str(system_ca))
        if verify_paths.cafile is None and system_ca.exists()
        else ssl.create_default_context()
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WorkoutError(f"Intervals.icu returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WorkoutError(f"Could not reach Intervals.icu: {exc.reason}") from exc


def upload_activity_file(file_path: Path, *, name: str, description: str = "", external_id: str = "") -> Any:
    """Upload a completed FIT/TCX/GPX activity using Intervals.icu multipart API."""
    if not file_path.exists():
        raise WorkoutError(f"Activity file not found: {file_path}")
    boundary = f"----IndoorTrainer{uuid.uuid4().hex}"
    file_bytes = file_path.read_bytes()
    parts = [
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode(),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    query_values = {"name": name}
    if description:
        query_values["description"] = description
    if external_id:
        query_values["external_id"] = external_id
    query = urllib.parse.urlencode(query_values)
    token = base64.b64encode(f"API_KEY:{get_api_key()}".encode()).decode("ascii")
    request = urllib.request.Request(
        f"{API_BASE}/athlete/0/activities?{query}",
        data=b"".join(parts),
        method="POST",
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "trainer-intervals-erg/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            payload = response.read()
            return json.loads(payload) if payload else {"status": response.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WorkoutError(f"Intervals.icu upload returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WorkoutError(f"Could not upload to Intervals.icu: {exc.reason}") from exc


def uploaded_activity_id(payload: Any) -> str | None:
    """Extract an activity ID from current and legacy Intervals upload responses."""
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        activities = payload.get("activities")
        if isinstance(activities, list):
            candidates.extend(activities)
    elif isinstance(payload, list):
        candidates.extend(payload)
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("id"):
            return str(candidate["id"])
    return None


def list_ride_events(date_value: dt.date) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"oldest": date_value.isoformat(), "newest": date_value.isoformat()})
    result = api_request(f"/athlete/0/events?{query}")
    events = result if isinstance(result, list) else []
    return [
        event
        for event in events
        if str(event.get("type") or "").lower() in {"ride", "virtualride", "virtual ride"}
        and str(event.get("description") or "").strip()
    ]


def workout_from_event(event: dict[str, Any]) -> Workout:
    return parse_workout_description(
        str(event.get("description") or ""),
        name=str(event.get("name") or "Intervals.icu ride"),
        event_id=str(event.get("id") or event.get("external_id") or ""),
        start_date_local=str(event.get("start_date_local") or ""),
    )


def load_workouts_from_plan(path: Path, date_value: dt.date | None = None) -> list[Workout]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkoutError(f"Could not read local plan {path}: {exc}") from exc
    workouts = []
    for event in plan.get("events") or []:
        if str(event.get("type") or "").lower() != "ride":
            continue
        if date_value and event.get("date") != date_value.isoformat():
            continue
        normalized = dict(event)
        normalized.setdefault("start_date_local", f"{event.get('date', '')}T{event.get('start_time', '00:00')}:00")
        workouts.append(workout_from_event(normalized))
    return workouts
