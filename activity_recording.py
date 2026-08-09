"""Crash-safe raw workout recording and standards-compliant FIT activity export."""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ActivitySample:
    timestamp: dt.datetime
    elapsed_seconds: float
    step_index: int
    step_name: str
    target_power: int | None
    values: dict[str, Any] = field(default_factory=dict)


class WorkoutRecorder:
    def __init__(self, workout_name: str, event_id: str, output_root: Path):
        self.workout_name = workout_name
        self.event_id = event_id
        self.started_at = dt.datetime.now(dt.timezone.utc)
        self._started_monotonic = time.monotonic()
        stamp = self.started_at.astimezone().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in workout_name).strip("_")[:60]
        self.directory = output_root / self.started_at.astimezone().strftime("%Y-%m-%d")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base_path = self.directory / f"{stamp}_{safe_name or 'indoor_ride'}"
        self.raw_path = self.base_path.with_suffix(".raw.jsonl")
        self.fit_path = self.base_path.with_suffix(".fit")
        self.samples: list[ActivitySample] = []
        self._raw_file = self.raw_path.open("a", encoding="utf-8")
        self.raw_event("metadata", {"workout_name": workout_name, "event_id": event_id})

    def raw_event(self, source: str, payload: dict[str, Any]) -> None:
        event = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - self._started_monotonic,
            "source": source,
            "data": payload,
        }
        self._raw_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._raw_file.flush()

    def sample(self, elapsed_seconds: float, step_index: int, step_name: str, target_power: int | None, values: dict[str, Any]) -> None:
        sample = ActivitySample(
            timestamp=dt.datetime.now(dt.timezone.utc),
            elapsed_seconds=elapsed_seconds,
            step_index=step_index,
            step_name=step_name,
            target_power=target_power,
            values=dict(values),
        )
        self.samples.append(sample)
        self.raw_event(
            "sample",
            {
                "elapsed_seconds": elapsed_seconds,
                "step_index": step_index,
                "step_name": step_name,
                "target_power": target_power,
                **values,
            },
        )

    def finish(self) -> Path:
        self._raw_file.close()
        if not self.samples:
            raise RuntimeError("No workout samples were recorded")
        self._write_fit()
        return self.fit_path

    def _write_fit(self) -> None:
        try:
            from fit_tool.fit_file_builder import FitFileBuilder
            from fit_tool.profile.messages.activity_message import ActivityMessage
            from fit_tool.profile.messages.event_message import EventMessage
            from fit_tool.profile.messages.file_id_message import FileIdMessage
            from fit_tool.profile.messages.lap_message import LapMessage
            from fit_tool.profile.messages.record_message import RecordMessage
            from fit_tool.profile.messages.session_message import SessionMessage
            from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Manufacturer, Sport, SubSport
        except ImportError as exc:
            raise RuntimeError("FIT export requires 'fit-tool'. Run: python3 -m pip install --user fit-tool") from exc

        builder = FitFileBuilder(auto_define=True, min_string_size=80)
        start_ms = round(self.started_at.timestamp() * 1000)
        end_ms = round(self.samples[-1].timestamp.timestamp() * 1000)

        file_id = FileIdMessage()
        file_id.type = FileType.ACTIVITY
        file_id.manufacturer = Manufacturer.DEVELOPMENT.value
        file_id.product = 1
        file_id.serial_number = 1
        file_id.time_created = start_ms
        builder.add(file_id)

        start_event = EventMessage()
        start_event.event = Event.TIMER
        start_event.event_type = EventType.START
        start_event.timestamp = start_ms
        builder.add(start_event)

        distance = 0.0
        previous_elapsed = self.samples[0].elapsed_seconds
        for sample in self.samples:
            values = sample.values
            speed = _number(values.get("speed"))
            supplied_distance = _number(values.get("distance"))
            if supplied_distance is not None:
                distance = max(distance, supplied_distance)
            elif speed is not None:
                distance += max(0.0, sample.elapsed_seconds - previous_elapsed) * speed
            previous_elapsed = sample.elapsed_seconds

            record = RecordMessage()
            record.timestamp = round(sample.timestamp.timestamp() * 1000)
            record.distance = distance
            if speed is not None:
                record.speed = max(0.0, speed)
            power = _number(values.get("power"))
            cadence = _number(values.get("cadence"))
            heart_rate = _number(values.get("heart_rate"))
            balance = _number(values.get("power_balance"))
            if power is not None:
                record.power = max(0, round(power))
            if cadence is not None:
                record.cadence = max(0, round(cadence))
            if heart_rate is not None:
                record.heart_rate = max(0, round(heart_rate))
            if balance is not None:
                record.left_right_balance = max(0.0, min(100.0, balance))
            builder.add(record)

        stop_event = EventMessage()
        stop_event.event = Event.TIMER
        stop_event.event_type = EventType.STOP_ALL
        stop_event.timestamp = end_ms
        builder.add(stop_event)

        for step_index in sorted({sample.step_index for sample in self.samples}):
            lap_samples = [sample for sample in self.samples if sample.step_index == step_index]
            builder.add(_summary_message(LapMessage(), lap_samples, Sport, SubSport, Event, EventType))

        session = _summary_message(SessionMessage(), self.samples, Sport, SubSport, Event, EventType)
        session.num_laps = len({sample.step_index for sample in self.samples})
        builder.add(session)

        activity = ActivityMessage()
        activity.timestamp = end_ms
        activity.total_timer_time = max(0.0, self.samples[-1].elapsed_seconds)
        activity.num_sessions = 1
        activity.type = Activity.MANUAL
        activity.event = Event.ACTIVITY
        activity.event_type = EventType.STOP
        builder.add(activity)
        builder.build().to_file(self.fit_path)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _values(samples: list[ActivitySample], key: str) -> list[float]:
    return [number for sample in samples if (number := _number(sample.values.get(key))) is not None]


def _summary_message(message: Any, samples: list[ActivitySample], Sport: Any, SubSport: Any, Event: Any, EventType: Any) -> Any:
    start_ms = round(samples[0].timestamp.timestamp() * 1000)
    end_ms = round(samples[-1].timestamp.timestamp() * 1000)
    elapsed = max(0.0, samples[-1].elapsed_seconds - samples[0].elapsed_seconds)
    speeds = _values(samples, "speed")
    powers = _values(samples, "power")
    cadences = _values(samples, "cadence")
    heart_rates = _values(samples, "heart_rate")
    distances = _values(samples, "distance")
    message.timestamp = end_ms
    message.start_time = start_ms
    message.total_elapsed_time = elapsed
    message.total_timer_time = elapsed
    message.total_distance = max(distances) - min(distances) if len(distances) > 1 else sum(speeds) if speeds else 0.0
    message.sport = Sport.CYCLING
    message.sub_sport = SubSport.INDOOR_CYCLING
    message.event = Event.LAP if message.__class__.__name__.startswith("Lap") else Event.SESSION
    message.event_type = EventType.STOP
    if powers:
        message.avg_power = round(statistics.fmean(powers))
        message.max_power = round(max(powers))
        message.total_work = round(sum(powers))
    if cadences:
        message.avg_cadence = round(statistics.fmean(cadences))
        message.max_cadence = round(max(cadences))
    if heart_rates:
        message.avg_heart_rate = round(statistics.fmean(heart_rates))
        message.max_heart_rate = round(max(heart_rates))
    if speeds:
        message.avg_speed = statistics.fmean(speeds)
        message.max_speed = max(speeds)
    return message
