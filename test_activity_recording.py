import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage

from activity_recording import WorkoutRecorder
from csc_cadence_sensor import parse_csc_measurement_full
from intervals_workout import upload_activity_file
from workout_sensors import RevolutionRateCalculator, WheelSpeedCalculator


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

