"""Trainer-independent ERG workout timeline and preview helpers."""

from __future__ import annotations

from dataclasses import dataclass

from intervals_workout import Workout, WorkoutError, WorkoutStep


INTERVAL_COUNTDOWN_CUES = ((5.0, 1), (2.0, 2), (1.0, 3))


def interval_countdown_cues(previous_remaining: float, current_remaining: float) -> list[int]:
    """Return pulse counts for countdown thresholds crossed since the previous update."""
    return [pulses for threshold, pulses in INTERVAL_COUNTDOWN_CUES if previous_remaining > threshold >= current_remaining]


@dataclass(frozen=True)
class TimelineState:
    step_index: int
    step: WorkoutStep
    elapsed_in_step: float
    remaining_in_step: float
    target_watts: int | None
    finished: bool = False


def step_power(step: WorkoutStep, ftp: int, progress: float) -> int | None:
    if step.mode == "freeride":
        return None
    progress = max(0.0, min(1.0, progress))
    start = float(step.start_value or 0)
    end = float(step.end_value if step.end_value is not None else start)
    value = start + (end - start) * progress
    return int(round(value * ftp / 100)) if step.value_unit == "percent" else int(round(value))


class WorkoutTimeline:
    def __init__(self, workout: Workout, ftp: int, max_watts: int = 600):
        if not 50 <= ftp <= 600:
            raise WorkoutError("FTP must be between 50 and 600 W")
        if not 100 <= max_watts <= 2000:
            raise WorkoutError("Maximum watt limit must be between 100 and 2000 W")
        self.workout = workout
        self.ftp = ftp
        self.max_watts = max_watts
        self._starts: list[int] = []
        elapsed = 0
        for step in workout.steps:
            self._starts.append(elapsed)
            elapsed += step.duration_seconds
            for power in (step_power(step, ftp, 0), step_power(step, ftp, 1)):
                if power is not None and (power < 0 or power > max_watts):
                    raise WorkoutError(
                        f"Step {step.name!r} requests {power} W, outside the safe limit 0-{max_watts} W"
                    )
        self.duration_seconds = elapsed

    def state_at(self, elapsed_seconds: float) -> TimelineState:
        elapsed_seconds = max(0.0, elapsed_seconds)
        if elapsed_seconds >= self.duration_seconds:
            step = self.workout.steps[-1]
            return TimelineState(len(self.workout.steps) - 1, step, step.duration_seconds, 0, None, True)
        for index, start in enumerate(self._starts):
            step = self.workout.steps[index]
            if elapsed_seconds < start + step.duration_seconds:
                inside = elapsed_seconds - start
                progress = inside / step.duration_seconds
                return TimelineState(
                    index,
                    step,
                    inside,
                    step.duration_seconds - inside,
                    step_power(step, self.ftp, progress),
                )
        raise AssertionError("Timeline lookup failed")


def format_seconds(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def workout_preview(timeline: WorkoutTimeline) -> str:
    lines = [
        f"Workout: {timeline.workout.name}",
        f"Duration: {format_seconds(timeline.duration_seconds)}",
        f"FTP: {timeline.ftp} W",
        f"Maximum allowed target: {timeline.max_watts} W",
        "",
    ]
    for index, step in enumerate(timeline.workout.steps, 1):
        start = step_power(step, timeline.ftp, 0)
        end = step_power(step, timeline.ftp, 1)
        if start is None:
            target = "freeride (ERG off)"
        elif start != end:
            target = f"{start}-{end} W ramp"
        else:
            target = f"{start} W"
        cadence = ""
        if step.cadence_min is not None:
            cadence = f" | cadence {step.cadence_min}"
            if step.cadence_max != step.cadence_min:
                cadence += f"-{step.cadence_max}"
            cadence += " rpm"
        lines.append(f"{index:>2}. {format_seconds(step.duration_seconds)} | {target}{cadence} | {step.name}")
    return "\n".join(lines)
