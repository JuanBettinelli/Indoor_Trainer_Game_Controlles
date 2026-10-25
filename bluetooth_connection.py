"""Reliable discovery of configured Bluetooth devices."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from bleak import BleakScanner


class BluetoothDeviceUnavailable(RuntimeError):
    pass


def _device_name(device: Any) -> str:
    return str(getattr(device, "name", "") or "").strip()


def select_discovered_device(devices: list[Any], address: str, name: str | None) -> tuple[Any | None, str | None]:
    """Prefer the saved address, then a unique exact/partial saved-name match."""
    address_key = address.strip().casefold()
    for device in devices:
        if str(getattr(device, "address", "") or "").strip().casefold() == address_key:
            return device, "address"

    name_key = (name or "").strip().casefold()
    if not name_key:
        return None, None
    exact = [device for device in devices if _device_name(device).casefold() == name_key]
    if len(exact) == 1:
        return exact[0], "name"
    partial = [device for device in devices if name_key in _device_name(device).casefold()]
    if len(partial) == 1:
        return partial[0], "name"
    return None, None


async def resolve_ble_device(
    address: str,
    name: str | None,
    *,
    attempts: int = 3,
    scan_timeout: float = 6.0,
    discover: Callable[..., Awaitable[list[Any]]] | None = None,
) -> Any:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if scan_timeout <= 0:
        raise ValueError("scan_timeout must be positive")
    discover = discover or BleakScanner.discover
    label = f"{name} ({address})" if name else address
    for attempt in range(1, attempts + 1):
        print(f"Scanning for trainer {label} (attempt {attempt}/{attempts}) ...")
        devices = list(await discover(timeout=scan_timeout))
        device, matched_by = select_discovered_device(devices, address, name)
        if device is not None:
            current_address = str(getattr(device, "address", "") or "")
            if matched_by == "name" and current_address.casefold() != address.casefold():
                print(f"Trainer found by name with current address {current_address}; using it for this workout.")
            else:
                print(f"Trainer found: {_device_name(device) or current_address}")
            return device
        if attempt < attempts:
            await asyncio.sleep(1.0)
    raise BluetoothDeviceUnavailable(
        f"Trainer {label} was not found after {attempts} scans. "
        "Wake the trainer by pedalling, keep it close to the Mac, and disconnect it from Wahoo, Zwift, "
        "Garmin, or other Bluetooth apps. If it was replaced or renamed, run 0_Setup_Devices.py again."
    )
