import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage

from activity_recording import WorkoutRecorder
from csc_cadence_sensor import parse_csc_measurement_full
from intervals_workout import upload_activity_file
from workout_sensors import RevolutionRateCalculator, WheelSpeedCalculator, WorkoutSensorHub


class ActivityRecordingTests(unittest.TestCase):
    def test_fit_and_raw_files_preserve_activity_data(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = WorkoutRecorder("Test Ride", "event-1", Path(temp))
            for second in range(6):
                recorder.sample(
                    second,
                    0 if second < 3 else 1,
                    "Step",
                    200,
                    {
                        "power": 200 + second,
                        "cadence": 90,
                        "speed": 8.0,
                        "distance": second * 8.0,
                        "heart_rate": 140,
                        "power_balance": 50.0,
                        "trainer_power": 195 + second,
                    },
                )
            fit_path = recorder.finish()
            fit_file = FitFile.from_file(fit_path)
            records = [record.message for record in fit_file.records if isinstance(record.message, RecordMessage)]
            self.assertEqual(6, len(records))
            self.assertEqual(200, records[0].power)
            self.assertEqual(140, records[0].heart_rate)
            raw_text = recorder.raw_path.read_text(encoding="utf-8")
            self.assertIn('"trainer_power": 195', raw_text)
            size_after_finish = recorder.raw_path.stat().st_size
            recorder.raw_event("indoor_trainer", {"trainer_power": 999})
            self.assertEqual(size_after_finish, recorder.raw_path.stat().st_size)
            self.assertEqual(fit_path, recorder.finish())

    def test_csc_wheel_and_crank_data_are_retained(self):
        payload = bytes([0x03]) + (1234).to_bytes(4, "little") + (500).to_bytes(2, "little") + (42).to_bytes(2, "little") + (600).to_bytes(2, "little")
        crank, wheel = parse_csc_measurement_full(payload)
        self.assertEqual(42, crank.cumulative_crank_revolutions)
        self.assertEqual(1234, wheel.cumulative_wheel_revolutions)

    def test_revolution_and_speed_calculations(self):
        cadence = RevolutionRateCalculator(2048.0)
        cadence.update(100, 1000)
        self.assertAlmostEqual(60.0, cadence.update(101, 3048))
        wheel = WheelSpeedCalculator(2.0)
        wheel.update(10, 1000)
        speed, distance = wheel.update(11, 2024)
        self.assertAlmostEqual(2.0, speed)
        self.assertAlmostEqual(2.0, distance)

    def test_power_meter_cadence_uses_cycling_power_tick_rate(self):
        hub = WorkoutSensorHub()
        hub.power_crank.update(100, 1000)
        self.assertAlmostEqual(60.0, hub.power_crank.update(101, 2024))

    def test_cadence_priority_is_power_meter_then_sensor_then_trainer(self):
        hub = WorkoutSensorHub()
        now = time.monotonic()
        hub.latest["trainer_cadence"] = 75.0
        snapshot = hub.snapshot()
        self.assertEqual(75.0, snapshot["cadence"])
        self.assertEqual("trainer", snapshot["cadence_source"])

        hub.latest["cadence_sensor_cadence"] = 82.0
        hub.updated["cadence_sensor"] = now
        snapshot = hub.snapshot()
        self.assertEqual(82.0, snapshot["cadence"])
        self.assertEqual("cadence_sensor", snapshot["cadence_source"])

        hub.latest["power_meter_cadence"] = 91.0
        hub.updated["power_meter_cadence"] = now
        snapshot = hub.snapshot()
        self.assertEqual(91.0, snapshot["cadence"])
        self.assertEqual("power_meter", snapshot["cadence_source"])

    def test_intervals_upload_is_multipart(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"id": "i123"}).encode()

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ride.fit"
            path.write_bytes(b"FITDATA")
            with patch("intervals_workout.get_api_key", return_value="secret"), patch(
                "intervals_workout.urllib.request.urlopen", return_value=Response()
            ) as mocked:
                result = upload_activity_file(path, name="Ride")
        request = mocked.call_args.args[0]
        self.assertEqual("i123", result["id"])
        self.assertIn("multipart/form-data", request.headers["Content-type"])
        self.assertIn(b"FITDATA", request.data)


if __name__ == "__main__":
    unittest.main()

