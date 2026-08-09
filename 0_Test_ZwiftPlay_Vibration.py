#!/usr/bin/env python3
"""Connect to configured Zwift Play controllers and test their vibration motors."""

from __future__ import annotations

import asyncio

from bleak import BleakClient

from runtime_devices import addresses_for
from zwift_play_to_keyboard import RIDE_ON, ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, ZwiftHapticHub


async def main() -> None:
    addresses = addresses_for("zwift_play") or []
    if not addresses:
        raise RuntimeError("No Zwift Play controllers configured in devices.json")

    hub = ZwiftHapticHub(enabled=True)
    clients: list[BleakClient] = []
    try:
        for index, address in enumerate(addresses, 1):
            client = BleakClient(address)
            await client.connect()
            clients.append(client)
            await client.write_gatt_char(ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, RIDE_ON, response=False)
            hub.register(f"play-{index}", client)
            print(f"Connected Play controller {index}: {address}")

        await asyncio.sleep(0.5)
        print("Single pulse...")
        await hub.pulse()
        await asyncio.sleep(1.0)
        print("Double pulse...")
        await hub.pulse(count=2)
        await asyncio.sleep(0.5)
    finally:
        for client in clients:
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

