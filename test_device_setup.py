import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn("speed_sensor", setup_devices.suggested_roles(cadence))
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
        self.assertIsNone(config["devices"]["speed_sensor"])
        self.assertEqual([], config["devices"]["zwift_click"])

    def test_device_can_be_assigned_to_multiple_roles(self):
        config = empty_config()
        trainer = setup_devices.ScannedDevice(
            "KICKR CORE", "T1", (setup_devices.FTMS_SERVICE, setup_devices.CSC_SERVICE)
        )
        setup_devices.assign_device(config, trainer, {"indoor_trainer", "cadence_sensor"})
        self.assertEqual("T1", config["devices"]["indoor_trainer"]["address"])
        self.assertEqual("T1", config["devices"]["cadence_sensor"]["address"])
        self.assertEqual(
            {"indoor_trainer", "cadence_sensor"},
            setup_devices.roles_for_address(config, "T1"),
        )

    def test_reassigning_device_removes_old_roles_but_keeps_other_devices(self):
        config = empty_config()
        first = setup_devices.ScannedDevice("Sensor A", "A", ())
        second = setup_devices.ScannedDevice("Sensor B", "B", ())
        setup_devices.assign_device(config, first, {"cadence_sensor", "zwift_play"})
        setup_devices.assign_device(config, second, {"heart_rate_sensor"})
        setup_devices.assign_device(config, first, {"power_meter"})
        self.assertIsNone(config["devices"]["cadence_sensor"])
        self.assertEqual([], config["devices"]["zwift_play"])
        self.assertEqual("A", config["devices"]["power_meter"]["address"])
        self.assertEqual("B", config["devices"]["heart_rate_sensor"]["address"])

    def test_assigning_empty_roles_removes_device(self):
        config = empty_config()
        play = setup_devices.ScannedDevice("Zwift Play", "P1", ())
        setup_devices.assign_device(config, play, {"zwift_play"})
        setup_devices.assign_device(config, play, set())
        self.assertEqual([], config["devices"]["zwift_play"])

    def test_device_first_selection_accepts_multiple_devices(self):
        config = empty_config()
        devices = [
            setup_devices.ScannedDevice("Trainer", "T1", ()),
            setup_devices.ScannedDevice("HRM", "H1", ()),
        ]
        with patch("builtins.input", return_value="1,2"):
            selected = setup_devices.choose_devices(devices, config)
        self.assertEqual(["T1", "H1"], [device.address for device in selected])

    def test_type_selection_accepts_multiple_roles(self):
        config = empty_config()
        device = setup_devices.ScannedDevice("Combined sensor", "C1", ())
        with patch("builtins.input", return_value="2,3"):
            roles = setup_devices.choose_roles_for_device(device, config)
        self.assertEqual({"power_meter", "cadence_sensor"}, roles)


if __name__ == "__main__":
    unittest.main()
