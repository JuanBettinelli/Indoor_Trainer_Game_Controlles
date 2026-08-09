#!/usr/bin/env python3
"""Scan, classify, select, and save Bluetooth devices used by this project."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bleak import BleakScanner

from device_config import (
    ALL_DEVICE_ROLES,
    DEFAULT_CONFIG_PATH,
    MULTI_DEVICE_ROLES,
    ROLE_LABELS,
    DeviceConfigError,
    empty_config,
    load_device_config,
)


FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"
HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
ZWIFT_SERVICE = "00000001-19ca-4651-86e5-fa29dcdd09d1"


@dataclass(frozen=True)
class ScannedDevice:
    name: str
    address: str
    service_uuids: tuple[str, ...]
    rssi: int | None = None

    def as_config_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "service_uuids": list(self.service_uuids),
        }


def normalize_uuid(value: str) -> str:
    value = value.strip().lower()
    if len(value) == 4:
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    if len(value) == 8:
        return f"{value}-0000-1000-8000-00805f9b34fb"
    return value


def suggested_roles(device: ScannedDevice) -> set[str]:
    name = device.name.lower()
    services = {normalize_uuid(value) for value in device.service_uuids}
    roles: set[str] = set()
    if FTMS_SERVICE in services or any(word in name for word in ("kickr", "tacx", "trainer", "direto", "suito", "neo ")):
        roles.add("indoor_trainer")
    if CYCLING_POWER_SERVICE in services or any(
        word in name for word in ("assioma", "favero", "rally", "vector", "4iiii", "stages", "magene", "x-cadey", "power meter")
    ):
        roles.add("power_meter")
    if CSC_SERVICE in services or "cadence" in name:
        roles.add("cadence_sensor")
    if CSC_SERVICE in services or "speed sensor" in name or "speed" in name:
        roles.add("speed_sensor")
    if HEART_RATE_SERVICE in services or any(word in name for word in ("heart rate", "heartrate", "hrm", "polar h", "tickr")):
        roles.add("heart_rate_sensor")
    if "zwift play" in name or (ZWIFT_SERVICE in services and "play" in name):
        roles.add("zwift_play")
    if "zwift click" in name or (ZWIFT_SERVICE in services and "click" in name):
        roles.add("zwift_click")
    return roles


async def scan_devices(timeout: float) -> list[ScannedDevice]:
    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as exc:
        raise DeviceConfigError(
            f"Bluetooth scan failed: {exc}. Check Bluetooth power and system permissions."
        ) from exc
    devices = []
    for address, pair in discovered.items():
        device, advertisement = pair
        name = device.name or getattr(advertisement, "local_name", None) or "Unknown device"
        services = tuple(sorted(normalize_uuid(value) for value in (advertisement.service_uuids or [])))
        rssi = getattr(advertisement, "rssi", None)
        devices.append(ScannedDevice(name, device.address or address, services, rssi))
    return sorted(devices, key=lambda item: (item.name.lower(), item.address))


def device_summary(device: ScannedDevice) -> str:
    roles = ", ".join(ROLE_LABELS[role] for role in ALL_DEVICE_ROLES if role in suggested_roles(device))
    signal = f" | RSSI {device.rssi}" if device.rssi is not None else ""
    return f"{device.name} | {device.address}{signal}" + (f" | suggested: {roles}" if roles else "")


def show_config(config: dict[str, Any], path: Path) -> None:
    print(f"Device configuration: {path}\n")
    for role in ALL_DEVICE_ROLES:
        value = config["devices"].get(role)
        records = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        if records:
            text = ", ".join(f"{item.get('name', 'Unknown')} ({item.get('address', '-')})" for item in records)
        else:
            text = "not configured"
        print(f"{ROLE_LABELS[role]:<28} {text}")


def roles_for_address(config: dict[str, Any], address: str) -> set[str]:
    roles: set[str] = set()
    for role in ALL_DEVICE_ROLES:
        value = config["devices"].get(role)
        records = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        if any(item.get("address") == address for item in records):
            roles.add(role)
    return roles


def remove_address(config: dict[str, Any], address: str) -> None:
    for role in ALL_DEVICE_ROLES:
        value = config["devices"].get(role)
        if role in MULTI_DEVICE_ROLES:
            config["devices"][role] = [
                item for item in (value or []) if item.get("address") != address
            ]
        elif isinstance(value, dict) and value.get("address") == address:
            config["devices"][role] = None


def assign_device(config: dict[str, Any], device: ScannedDevice, roles: set[str]) -> None:
    invalid = roles - set(ALL_DEVICE_ROLES)
    if invalid:
        raise DeviceConfigError(f"Unknown device role(s): {', '.join(sorted(invalid))}")
    remove_address(config, device.address)
    record = device.as_config_record()
    for role in roles:
        if role in MULTI_DEVICE_ROLES:
            records = config["devices"].setdefault(role, [])
            if not any(item.get("address") == device.address for item in records):
                records.append(dict(record))
        else:
            config["devices"][role] = dict(record)


def choose_devices(devices: list[ScannedDevice], config: dict[str, Any]) -> list[ScannedDevice]:
    print("\nAvailable Bluetooth devices:")
    print("'*' indicates an automatic type suggestion; '[saved]' means already configured.\n")
    for index, device in enumerate(devices, 1):
        suggested = "*" if suggested_roles(device) else " "
        saved = " [saved]" if roles_for_address(config, device.address) else ""
        print(f"{index:>2}{suggested} {device_summary(device)}{saved}")
    answer = input("\nSelect devices to add or update (comma-separated, Enter to keep all unchanged): ").strip()
    if not answer:
        return []
    try:
        indexes = [int(part.strip()) for part in answer.split(",")]
        if not indexes or len(indexes) != len(set(indexes)):
            raise ValueError
        return [devices[index - 1] for index in indexes]
    except (ValueError, IndexError) as exc:
        raise DeviceConfigError("Invalid device selection") from exc


def choose_roles_for_device(device: ScannedDevice, config: dict[str, Any]) -> set[str]:
    current = roles_for_address(config, device.address)
    suggested = suggested_roles(device)
    defaults = current or suggested
    print(f"\nAssign type(s) for: {device.name} ({device.address})")
    for index, role in enumerate(ALL_DEVICE_ROLES, 1):
        markers = []
        if role in suggested:
            markers.append("suggested")
        if role in current:
            markers.append("current")
        suffix = f" [{' / '.join(markers)}]" if markers else ""
        print(f"{index}. {ROLE_LABELS[role]}{suffix}")
    default_text = ", ".join(str(ALL_DEVICE_ROLES.index(role) + 1) for role in ALL_DEVICE_ROLES if role in defaults)
    prompt = "Select one or more types (comma-separated)"
    if default_text:
        prompt += f" [default: {default_text}]"
    prompt += "; '-' removes this device: "
    answer = input(prompt).strip()
    if answer == "-":
        return set()
    if not answer:
        if defaults:
            return set(defaults)
        raise DeviceConfigError(f"No type selected for {device.name}")
    try:
        indexes = [int(part.strip()) for part in answer.split(",")]
        if not indexes or len(indexes) != len(set(indexes)):
            raise ValueError
        return {ALL_DEVICE_ROLES[index - 1] for index in indexes}
    except (ValueError, IndexError) as exc:
        raise DeviceConfigError(f"Invalid type selection for {device.name}") from exc


def atomic_write(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def configure(args: argparse.Namespace) -> None:
    config = empty_config() if args.reset else load_device_config(args.output)
    print(f"Scanning for Bluetooth devices for {args.scan_seconds:g} seconds...")
    devices = await scan_devices(args.scan_seconds)
    if not devices:
        raise DeviceConfigError("No Bluetooth devices found")
    print(f"Found {len(devices)} devices.")
    selected = choose_devices(devices, config)
    for device in selected:
        assign_device(config, device, choose_roles_for_device(device, config))
    config["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    print("\nProposed configuration:\n")
    show_config(config, args.output)
    if not args.yes and input("\nSave this configuration? [y/N]: ").strip().lower() not in {"y", "yes", "j", "ja"}:
        print("Cancelled. No file was changed.")
        return
    atomic_write(args.output, config)
    print(f"\nSaved device configuration to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--scan-seconds", type=float, default=8.0)
    parser.add_argument("--show", action="store_true", help="Show saved devices without scanning")
    parser.add_argument("--reset", action="store_true", help="Start with an empty configuration")
    parser.add_argument("--yes", action="store_true", help="Save without the final confirmation")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.show:
            show_config(load_device_config(args.output), args.output)
        else:
            if args.scan_seconds <= 0:
                raise DeviceConfigError("Scan duration must be positive")
            asyncio.run(configure(args))
        return 0
    except (DeviceConfigError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
