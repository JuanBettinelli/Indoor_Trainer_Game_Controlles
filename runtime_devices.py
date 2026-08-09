"""Resolve runtime device addresses from the local devices.json file."""

from __future__ import annotations

from device_config import DEFAULT_CONFIG_PATH, configured_address, configured_addresses, load_device_config


LEGACY_INDOOR_TRAINER_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"
LEGACY_CADENCE_SENSOR_ADDRESS = "5AAA25D1-0D9E-C93A-F661-6AC44233763A"


def address_for(role: str, legacy_address: str = "") -> str:
    """Return a configured address, using legacy defaults only before setup exists."""
    config_exists = DEFAULT_CONFIG_PATH.exists()
    address = configured_address(load_device_config(), role)
    if address:
        return address
    return "" if config_exists else legacy_address


def addresses_for(role: str) -> list[str] | None:
    """Return saved addresses; None means no devices.json exists yet."""
    if not DEFAULT_CONFIG_PATH.exists():
        return None
    return configured_addresses(load_device_config(), role)


INDOOR_TRAINER_ADDRESS = address_for("indoor_trainer", LEGACY_INDOOR_TRAINER_ADDRESS)
POWER_METER_ADDRESS = address_for("power_meter")
CADENCE_SENSOR_ADDRESS = address_for("cadence_sensor", LEGACY_CADENCE_SENSOR_ADDRESS)
SPEED_SENSOR_ADDRESS = address_for("speed_sensor")
HEART_RATE_SENSOR_ADDRESS = address_for("heart_rate_sensor")

