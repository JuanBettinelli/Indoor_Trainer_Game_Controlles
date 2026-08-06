import datetime as dt
import unittest

from erg_workout_runner import WorkoutTimeline, step_power, workout_preview
from intervals_workout import WorkoutError, parse_workout_description


class ParserTests(unittest.TestCase):
    def test_repeats_ramps_percent_watts_and_freeride(self):
        description = """Warmup
- Easy 10m 50%
- Ramp 5m ramp 50-75%

Main 2x
- Work 3m 250w 90-100rpm
- Rest 2m 55%

Test
- Free 1m freeride
"""
        workout = parse_workout_description(description, name="Test")
        self.assertEqual(7, len(workout.steps))
        self.assertEqual(26 * 60, workout.duration_seconds)
        self.assertEqual("ramp", workout.steps[1].mode)
        self.assertEqual((90, 100), (workout.steps[2].cadence_min, workout.steps[2].cadence_max))
        self.assertEqual("freeride", workout.steps[-1].mode)

    def test_hr_and_distance_targets_are_rejected(self):
        for line in ("- Endurance 20m Z2 HR", "- Endurance 10km Z2"):
            with self.subTest(line=line), self.assertRaises(WorkoutError):
                parse_workout_description(f"Main\n{line}")

    def test_percent_range_without_ramp_uses_midpoint(self):
        workout = parse_workout_description("Main\n- Tempo 10m 80-90%")
        self.assertEqual(85, workout.steps[0].start_value)
        self.assertEqual(85, workout.steps[0].end_value)


class TimelineTests(unittest.TestCase):
    def test_timeline_targets_and_finish(self):
        workout = parse_workout_description("Main\n- Ramp 10s ramp 50-100%\n- Free 5s freeride")
        timeline = WorkoutTimeline(workout, ftp=200, max_watts=300)
        self.assertEqual(100, timeline.state_at(0).target_watts)
        self.assertEqual(150, timeline.state_at(5).target_watts)
        self.assertIsNone(timeline.state_at(11).target_watts)
        self.assertTrue(timeline.state_at(15).finished)

    def test_power_limit_is_enforced(self):
        workout = parse_workout_description("Main\n- Sprint 10s 200%")
        with self.assertRaises(WorkoutError):
            WorkoutTimeline(workout, ftp=300, max_watts=500)

    def test_preview_contains_resolved_watts(self):
        workout = parse_workout_description("Main\n- Tempo 1m 80%", name="Tempo")
        preview = workout_preview(WorkoutTimeline(workout, ftp=250))
        self.assertIn("200 W", preview)
        self.assertIn("Tempo", preview)


if __name__ == "__main__":
    unittest.main()

