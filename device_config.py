"""Shared Bluetooth device configuration for indoor trainer scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).with_name("devices.json")
SCHEMA_VERSION = 1

SINGLE_DEVICE_ROLES = (
    "indoor_trainer",
    "power_meter",
    "cadence_sensor",
    "speed_sensor",
    "heart_rate_sensor",
)
MULTI_DEVICE_ROLES = ("zwift_play", "zwift_click")
ALL_DEVICE_ROLES = SINGLE_DEVICE_ROLES + MULTI_DEVICE_ROLES

ROLE_LABELS = {
    "indoor_trainer": "Indoor trainer",
    "power_meter": "Power meter",
    "cadence_sensor": "Cadence sensor",
    "speed_sensor": "Speed sensor",
    "heart_rate_sensor": "Heart-rate sensor",
    "zwift_play": "Zwift Play controller(s)",
    "zwift_click": "Zwift Click controller(s)",
}


class DeviceConfigError(RuntimeError):
    pass


def empty_config() -> dict[str, Any]:
    devices: dict[str, Any] = {role: None for role in SINGLE_DEVICE_ROLES}
    devices.update({role: [] for role in MULTI_DEVICE_ROLES})
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "devices": devices}


def load_device_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_config()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeviceConfigError(f"Could not read device configuration {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("schema_version") != SCHEMA_VERSION:
        raise DeviceConfigError(f"Unsupported device configuration schema in {path}")
    devices = config.get("devices")
    if not isinstance(devices, dict):
        raise DeviceConfigError(f"Missing devices object in {path}")
    normalized = empty_config()
    normalized["updated_at"] = config.get("updated_at")
    for role in SINGLE_DEVICE_ROLES:
        value = devices.get(role)
        normalized["devices"][role] = value if isinstance(value, dict) else None
    for role in MULTI_DEVICE_ROLES:
        value = devices.get(role)
        normalized["devices"][role] = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    return normalized


def configured_devices(config: dict[str, Any], role: str) -> list[dict[str, Any]]:
    if role not in ALL_DEVICE_ROLES:
        raise DeviceConfigError(f"Unknown device role: {role}")
    value = config.get("devices", {}).get(role)
    if role in MULTI_DEVICE_ROLES:
        return list(value or [])
    return [value] if isinstance(value, dict) else []


def configured_address(config: dict[str, Any], role: str) -> str | None:
    devices = configured_devices(config, role)
    return str(devices[0].get("address")) if devices and devices[0].get("address") else None


def configured_addresses(config: dict[str, Any], role: str) -> list[str]:
    return [str(item["address"]) for item in configured_devices(config, role) if item.get("address")]
