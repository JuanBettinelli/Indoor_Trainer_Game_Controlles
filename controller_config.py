"""Shared keyboard mappings for Zwift Play and Zwift Click controllers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).with_name("controller_mapping.json")
SCHEMA_VERSION = 1

PLAY_BUTTONS = ("Y", "Z", "A", "B", "Side", "On/Off", "right_paddle", "left_paddle")
CLICK_BUTTONS = ("Plus", "Minus")
PROFILE_BUTTONS = {
    "zwift_play_left": PLAY_BUTTONS,
    "zwift_play_right": PLAY_BUTTONS,
    "zwift_click": CLICK_BUTTONS,
}
PROFILE_LABELS = {
    "zwift_play_left": "Zwift Play — left controller",
    "zwift_play_right": "Zwift Play — right controller",
    "zwift_click": "Zwift Click",
}

DEFAULT_MAPPINGS: dict[str, dict[str, str]] = {
    "zwift_play_left": {
        "Y": "up",
        "Z": "left",
        "A": "right",
        "B": "down",
        "Side": "q",
        "On/Off": "escape",
        "right_paddle": "left",
        "left_paddle": "left",
    },
    "zwift_play_right": {
        "Y": "x",
        "Z": "y",
        "A": "a",
        "B": "b",
        "Side": "e",
        "On/Off": "enter",
        "right_paddle": "right",
        "left_paddle": "right",
    },
    "zwift_click": {"Plus": "right", "Minus": "left"},
}


class ControllerConfigError(RuntimeError):
    pass


def default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mappings": {profile: dict(mapping) for profile, mapping in DEFAULT_MAPPINGS.items()},
    }


def load_controller_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControllerConfigError(f"Could not read controller configuration {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ControllerConfigError(f"Unsupported controller configuration schema in {path}")
    mappings = raw.get("mappings")
    if not isinstance(mappings, dict):
        raise ControllerConfigError(f"Missing mappings object in {path}")

    normalized = {"schema_version": SCHEMA_VERSION, "mappings": {}}
    for profile, buttons in PROFILE_BUTTONS.items():
        value = mappings.get(profile)
        if not isinstance(value, dict):
            normalized["mappings"][profile] = dict(DEFAULT_MAPPINGS[profile])
            continue
        normalized["mappings"][profile] = {
            button: key
            for button, key in value.items()
            if button in buttons and isinstance(key, str) and key.strip()
        }
    return normalized


def mappings(path: Path = DEFAULT_CONFIG_PATH) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    config = load_controller_config(path)
    values = config["mappings"]
    return (
        dict(values["zwift_play_left"]),
        dict(values["zwift_play_right"]),
        dict(values["zwift_click"]),
    )

