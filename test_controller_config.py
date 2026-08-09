import json
import tempfile
import unittest
from pathlib import Path

from controller_config import (
    CLICK_BUTTONS,
    DEFAULT_MAPPINGS,
    default_config,
    load_controller_config,
    mappings,
    normalize_key_name,
    vibration_enabled,
)


class ControllerConfigTests(unittest.TestCase):
    def test_keyboard_aliases_use_pynput_names(self):
        self.assertEqual("esc", normalize_key_name("escape"))
        self.assertEqual("enter", normalize_key_name("RETURN"))
        self.assertEqual("a", normalize_key_name("A"))

    def test_defaults_include_play_and_click(self):
        config = default_config()
        self.assertEqual("up", config["mappings"]["zwift_play_left"]["Y"])
        self.assertEqual("right", config["mappings"]["zwift_click"]["Plus"])
        self.assertEqual(("Plus", "Minus"), CLICK_BUTTONS)

    def test_round_trip_preserves_disabled_button(self):
        config = default_config()
        del config["mappings"]["zwift_click"]["Minus"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controller_mapping.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            _, _, click = mappings(path)
        self.assertNotIn("Minus", click)

    def test_missing_profile_gets_defaults(self):
        config = default_config()
        del config["mappings"]["zwift_click"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controller_mapping.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_controller_config(path)
        self.assertEqual(DEFAULT_MAPPINGS["zwift_click"], loaded["mappings"]["zwift_click"])

    def test_vibration_setting_round_trip(self):
        config = default_config()
        config["settings"]["vibration_enabled"] = False
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controller_mapping.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertFalse(vibration_enabled(path))


if __name__ == "__main__":
    unittest.main()
