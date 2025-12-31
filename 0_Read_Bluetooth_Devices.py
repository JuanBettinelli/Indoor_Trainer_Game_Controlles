"""
An example script which lists all available bluetooth devices. Use this to obtain the device_address used in other
scripts
"""

import asyncio
from bleak import BleakScanner


async def scan_devices():
    """Scan for Bluetooth devices."""
    devices = await BleakScanner.discover()
    for device in devices:
        print(f"{device.name}: {device.address}")


if __name__ == "__main__":
    asyncio.run(scan_devices())