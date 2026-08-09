import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from device_config import configured_address, configured_addresses, empty_config, load_device_config


SPEC = importlib.util.spec_from_file_location("setup_devices", Path(__file__).with_name("0_Setup_Devices.py"))
setup_devices = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = setup_devices
SPEC.loader.exec_module(setup_devices)


class DeviceSetupTests(unittest.TestCase):
    def test_service_uuid_classification(self):
        trainer = setup_devices.ScannedDevice("KICKR CORE", "trainer", (setup_devices.FTMS_SERVICE,))
        pedals = setup_devices.ScannedDevice("Favero Assioma", "power", (setup_devices.CYCLING_POWER_SERVICE,))
        cadence = setup_devices.ScannedDevice("Garmin Cadence", "cadence", ("1816",))
        hrm = setup_devices.ScannedDevice("Polar H10", "hr", ("180d",))
        self.assertIn("indoor_trainer", setup_devices.suggested_roles(trainer))
        self.assertIn("power_meter", setup_devices.suggested_roles(pedals))
        self.assertIn("cadence_sensor", setup_devices.suggested_roles(cadence))
        self.assertIn("heart_rate_sensor", setup_devices.suggested_roles(hrm))

    def test_zwift_name_classification(self):
        play = setup_devices.ScannedDevice("Zwift Play", "play", (setup_devices.ZWIFT_SERVICE,))
        click = setup_devices.ScannedDevice("Zwift Click", "click", (setup_devices.ZWIFT_SERVICE,))
        self.assertEqual({"zwift_play"}, setup_devices.suggested_roles(play))
        self.assertEqual({"zwift_click"}, setup_devices.suggested_roles(click))

    def test_config_round_trip_and_accessors(self):
        config = empty_config()
        config["devices"]["indoor_trainer"] = {"name": "KICKR", "address": "T1"}
        config["devices"]["zwift_play"] = [
            {"name": "Play L", "address": "P1"},
            {"name": "Play R", "address": "P2"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "devices.json"
            setup_devices.atomic_write(path, config)
            loaded = load_device_config(path)
        self.assertEqual("T1", configured_address(loaded, "indoor_trainer"))
        self.assertEqual(["P1", "P2"], configured_addresses(loaded, "zwift_play"))

    def test_missing_config_returns_empty_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            config = load_device_config(Path(temp) / "missing.json")
        self.assertIsNone(config["devices"]["power_meter"])
        self.assertEqual([], config["devices"]["zwift_click"])


if __name__ == "__main__":
    unittest.main()
