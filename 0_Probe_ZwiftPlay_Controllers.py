"""
BLE probe for Zwift Play controller on macOS using bleak.

What it does:
1) Scans for nearby BLE devices and picks the first whose name contains TARGET_NAME.
2) Connects, prints all services/characteristics.
3) Subscribes to every characteristic that supports notifications and prints payloads when you press buttons.

Run:
  python3 Zwift_Play.py

Tips:
- Disconnect the controller from Zwift/BikeControl first (BLE often allows a single connection).
- While running, press buttons and observe the hex payloads; we’ll map bits to buttons from this output.
"""

import asyncio
from bleak import BleakClient, BleakScanner

TARGET_NAME = "Zwift"  # substring match; adjust if needed
# Optional: pin to a specific address to target left/right controller explicitly
# Example: TARGET_ADDRESS = "ABA8A512-21BB-6C25-1BC6-F9A03A4D68BC"
TARGET_ADDRESS = "E266B97E-3F81-9B82-20D6-CF3E4C2C7618" # "ABA8A512-21BB-6C25-1BC6-F9A03A4D68BC" # E266B97E-3F81-9B82-20D6-CF3E4C2C7618
RUN_SECONDS = 90        # how long to listen for notifications


async def find_device():
	print("Scanning for BLE devices...")
	devices = await BleakScanner.discover(timeout=6.0)
	for d in devices:
		print(f"  found: {d.name} | {d.address}")
	if TARGET_ADDRESS:
		match = next((d for d in devices if d.address == TARGET_ADDRESS), None)
	else:
		match = next((d for d in devices if d.name and TARGET_NAME.lower() in d.name.lower()), None)
	if not match:
		print(f"No device matching '{TARGET_NAME}' found. Is it on and disconnected from other apps?")
	return match


async def main():
	device = await find_device()
	if not device:
		return

	print(f"\nConnecting to {device.name} ({device.address})...")
	async with BleakClient(device) as client:
		print("Connected. Dumping services/characteristics:\n")
		for svc in client.services:
			print(f"Service {svc.uuid}")
			for ch in svc.characteristics:
				props = ",".join(ch.properties)
				print(f"  Char {ch.uuid} [{props}]")

		print("\nSubscribing to all notify/indicate characteristics... Press buttons now.\n")

		handlers = []
		async def make_handler(ch):
			async def _start():
				async def _handler(_, data: bytearray):
					hexstr = data.hex(" ")
					print(f"{ch.uuid}: len={len(data)} data={hexstr}")

				await client.start_notify(ch, _handler)
				return _handler
			return await _start()

		# Iterate characteristics per service (macOS corebluetooth returns proper properties here)
		for svc in client.services:
			for ch in svc.characteristics:
				if not hasattr(ch, "properties"):
					continue
				if not set(ch.properties) & {"notify", "indicate"}:
					continue
				try:
					handler = await make_handler(ch)
					handlers.append((ch, handler))
					print(f"  subscribed to {ch.uuid} [{','.join(ch.properties)}]")
				except Exception as e:  # noqa: BLE
					print(f"  failed to subscribe {ch.uuid}: {e}")

		# Try reading known chars to see if device requires a poke
		read_targets = ["00000004-19ca-4651-86e5-fa29dcdd09d1", "00000006-19ca-4651-86e5-fa29dcdd09d1"]
		for target in read_targets:
			try:
				val = await client.read_gatt_char(target)
				print(f"read {target}: {val.hex(' ')}")
			except Exception as e:
				print(f"read {target} failed: {e}")

				# Some devices need an enable/ping; try a few gentle one-byte writes (best-effort)
				enable_payloads = [b"\x01", b"\x02", b"\x03", b"\x04"]
				targets = [
					"00000002-19ca-4651-86e5-fa29dcdd09d1",  # notify char (try wake)
					"00000003-19ca-4651-86e5-fa29dcdd09d1",  # write-without-response
					"00000006-19ca-4651-86e5-fa29dcdd09d1",  # control?
				]
				for target in targets:
					for payload in enable_payloads:
						try:
							await client.write_gatt_char(target, payload, response=False)
							print(f"wrote {payload.hex(' ')} to {target}")
						except Exception as e:
							print(f"write to {target} failed: {e}")

		try:
			await asyncio.sleep(RUN_SECONDS)
		finally:
			for ch_uuid, _handler in handlers:
				await client.stop_notify(ch_uuid)
			print("Stopped listening. Disconnecting.")


if __name__ == "__main__":
	asyncio.run(main())
