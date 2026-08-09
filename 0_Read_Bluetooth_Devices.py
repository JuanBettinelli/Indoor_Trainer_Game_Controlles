"""
An example script which lists all available bluetooth devices. Use this to obtain the device_address used in other
scripts
"""

import asyncio
from bleak import BleakScanner

from device_config import ALL_DEVICE_ROLES, ROLE_LABELS, configured_addresses, load_device_config


async def scan_devices():
    """Scan for Bluetooth devices."""
    config = load_device_config()
    roles_by_address = {
        address: [ROLE_LABELS[role] for role in ALL_DEVICE_ROLES if address in configured_addresses(config, role)]
        for role in ALL_DEVICE_ROLES
        for address in configured_addresses(config, role)
    }
    devices = await BleakScanner.discover()
    for device in devices:
        saved_as = ", ".join(roles_by_address.get(device.address, []))
        marker = f" [configured: {saved_as}]" if saved_as else ""
        print(f"{device.name}: {device.address}{marker}")


if __name__ == "__main__":
    asyncio.run(scan_devices())
