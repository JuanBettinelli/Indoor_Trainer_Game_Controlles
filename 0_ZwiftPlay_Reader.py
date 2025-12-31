"""
Zwift Play button reader (macOS) using Bleak

- Connects to multiple Zwift Play controllers simultaneously
- Subscribes to Zwift custom service notifications from all controllers
- Sends the required handshake (RideOn + start-play) to each
- Decodes Play button events (protobuf-varint style) without external deps

Run:
  python3 Zwift_Play_Reader.py                    # Auto-detect all Zwift controllers
  python3 Zwift_Play_Reader.py --name "Zwift"    # Filter by name substring
  python3 Zwift_Play_Reader.py AA:BB:CC:DD:EE:FF # Connect to specific address

Notes:
- Make sure the controllers are not connected to Zwift or other apps.
- Supports connecting to 2 or more controllers at the same time.
- Each controller's input is labeled with its name/address for easy identification.
"""

import asyncio
import sys
from typing import Dict, Tuple, List, Optional

from bleak import BleakClient, BleakScanner

# Zwift UUIDs extracted from swiftcontrol (BikeControl)
ZWIFT_CUSTOM_SERVICE_UUID = "00000001-19CA-4651-86E5-FA29DCDD09D1"
ZWIFT_ASYNC_CHARACTERISTIC_UUID = "00000002-19CA-4651-86E5-FA29DCDD09D1"  # notify
ZWIFT_SYNC_RX_CHARACTERISTIC_UUID = "00000003-19CA-4651-86E5-FA29DCDD09D1"  # write w/o response
ZWIFT_SYNC_TX_CHARACTERISTIC_UUID = "00000004-19CA-4651-86E5-FA29DCDD09D1"  # indicate/notify

# Message types (per swiftcontrol constants)
PLAY_NOTIFICATION_MESSAGE_TYPE = 0x07
EMPTY_MESSAGE_TYPE = 0x15
BATTERY_LEVEL_TYPE = 0x19

# Handshake bytes (per swiftcontrol constants)
RIDE_ON = bytes([0x52, 0x69, 0x64, 0x65, 0x4F, 0x6E])  # "RideOn"
RESPONSE_START_PLAY = bytes([0x01, 0x04])

# Discovery parameters
TARGET_NAME_SUBSTR = "Zwift"   # picks all with this in name by default
TARGET_ADDRESS: Optional[str] = None  # e.g., "AA:BB:CC:DD:EE:FF" or macOS UUID
SCAN_TIMEOUT = 6.0
RUN_SECONDS = 90
MAX_CONTROLLERS = 2  # Set to higher number to support more

# PlayKeyPadStatus field numbers (protobuf varint)
FIELD_RIGHT_PAD = 1  # 0=ON (right), 1=OFF (left)
FIELD_Y_UP = 2       # 0=ON pressed
FIELD_Z_LEFT = 3
FIELD_A_RIGHT = 4
FIELD_B_DOWN = 5
FIELD_SHIFT = 6
FIELD_ON = 7
FIELD_ANALOG_LR = 8  # signed varint (zigzag)
FIELD_ANALOG_UD = 9  # signed varint (zigzag)

# Enum mapping: ON=0, OFF=1 per swiftcontrol PlayButtonStatus
ON = 0
OFF = 1


def _read_varint(buf: bytes, i: int) -> Tuple[int, int]:
    """Read protobuf varint starting at index i. Returns (value, next_index)."""
    shift = 0
    result = 0
    while True:
        if i >= len(buf):
            raise ValueError("Unexpected end of buffer while reading varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63:
            raise ValueError("Varint too long")
    return result, i


def _zigzag_decode(n: int) -> int:
    """Decode protobuf zigzag-encoded signed integer (int32)."""
    return (n >> 1) ^ (-(n & 1))


def parse_play_keypad_status(message: bytes) -> Dict[int, int]:
    """Parse PlayKeyPadStatus protobuf (all fields are varints).

    Returns a dict mapping field_number -> int_value.
    """
    i = 0
    fields: Dict[int, int] = {}
    while i < len(message):
        key, i = _read_varint(message, i)
        field_number = key >> 3
        wire_type = key & 0x7
        if wire_type != 0:
            # We only expect varints here (enums and signed int via zigzag)
            raise ValueError(f"Unexpected wire type {wire_type} for field {field_number}")
        val, i = _read_varint(message, i)
        fields[field_number] = val
    return fields


def decode_buttons(fields: Dict[int, int]) -> Dict[str, List[str]]:
    """Return a human-friendly dict with side + pressed buttons.

    Example: {"side": "right", "buttons": ["Y", "A"], "paddles": ["right"]}
    """
    side = "right" if fields.get(FIELD_RIGHT_PAD, OFF) == ON else "left"
    pressed: List[str] = []
    paddles: List[str] = []

    if fields.get(FIELD_Y_UP) == ON:
        pressed.append("Y")
    if fields.get(FIELD_Z_LEFT) == ON:
        pressed.append("Z")
    if fields.get(FIELD_A_RIGHT) == ON:
        pressed.append("A")
    if fields.get(FIELD_B_DOWN) == ON:
        pressed.append("B")
    if fields.get(FIELD_SHIFT) == ON:
        pressed.append("Side")
    if fields.get(FIELD_ON) == ON:
        pressed.append("On/Off")

    # Paddles via analog LR reaching full deflection (±100)
    analog_lr = fields.get(FIELD_ANALOG_LR)
    if analog_lr is not None:
        analog_lr = _zigzag_decode(analog_lr)
        if abs(analog_lr) >= 100:
            paddles.append("right" if side == "right" else "left")

    return {"side": side, "buttons": pressed, "paddles": paddles}


async def find_devices() -> List[object]:
    """Find all matching Zwift Play devices."""
    print(f"Scanning for BLE devices (timeout={SCAN_TIMEOUT}s)...")
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
    print(f"Found {len(devices)} total devices:")
    for d in devices:
        print(f"  • {d.name or '(unnamed)'} | {d.address}")

    matches: List[object] = []
    if TARGET_ADDRESS:
        # Single specific address
        match = next((d for d in devices if d.address == TARGET_ADDRESS), None)
        if match:
            matches.append(match)
        else:
            print(f"No device at address {TARGET_ADDRESS}")
    else:
        # All matching the name substring
        matches = [d for d in devices if d.name and TARGET_NAME_SUBSTR.lower() in d.name.lower()]
        matches = matches[:MAX_CONTROLLERS]  # Limit to MAX_CONTROLLERS

    if not matches:
        print(f"No devices matching name substring '{TARGET_NAME_SUBSTR}'. Are they on and disconnected?")
        return []
    
    print(f"\nWill connect to {len(matches)} device(s):")
    for m in matches:
        print(f"  • {m.name} ({m.address})")
    return matches


async def connect_to_controller(device: object, device_id: int) -> None:
    """Connect to a single controller and listen to its events."""
    device_label = f"[{device.name or 'Unknown'}]"
    
    print(f"\n{device_label} Connecting ({device.address})...")
    try:
        async with BleakClient(device) as client:
            print(f"{device_label} Connected. Discovering services...")
            # Ensure services are resolved
            services = client.services
            if services is None:
                await client.get_services()
                services = client.services

            custom_service = services.get_service(ZWIFT_CUSTOM_SERVICE_UUID) if services else None
            if not custom_service:
                # Some firmware advertises only the short ride service; still try by UUIDs directly
                print(f"{device_label} Zwift custom service not explicitly found; proceeding with known characteristic UUIDs.")

            # Setup notification handlers
            async def on_async(_, data: bytearray):
                b = bytes(data)
                if not b:
                    return
                msg_type = b[0]
                payload = b[1:]
                if msg_type == PLAY_NOTIFICATION_MESSAGE_TYPE:
                    try:
                        fields = parse_play_keypad_status(payload)
                        info = decode_buttons(fields)
                        if info["buttons"] or info["paddles"]:
                            print(f"{device_label} PLAY {info['side']}: buttons={info['buttons']} paddles={info['paddles']}")
                        else:
                            # nothing pressed update; can be spammy
                            pass
                    except Exception as e:
                        print(f"{device_label} Error parsing PlayKeyPadStatus: {e}; raw={payload.hex(' ')}")
                elif msg_type == BATTERY_LEVEL_TYPE:
                    if len(payload) >= 2:
                        print(f"{device_label} Battery: {payload[1]}%")
                    else:
                        print(f"{device_label} Battery notification: {payload.hex(' ')}")
                elif msg_type == EMPTY_MESSAGE_TYPE:
                    # Idle/keep-alive
                    pass
                else:
                    print(f"{device_label} Unknown msg type {msg_type:#04x}, data={b.hex(' ')}")

            async def on_sync_tx(_, data: bytearray):
                # Handshake / device responses appear here as well
                print(f"{device_label} SYNC_TX: {bytes(data).hex(' ')}")

            # Subscribe
            try:
                await client.start_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID, on_async)
                print(f"{device_label} Subscribed to ASYNC {ZWIFT_ASYNC_CHARACTERISTIC_UUID}")
            except Exception as e:
                print(f"{device_label} Failed to subscribe ASYNC: {e}")

            try:
                await client.start_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID, on_sync_tx)
                print(f"{device_label} Subscribed to SYNC_TX {ZWIFT_SYNC_TX_CHARACTERISTIC_UUID}")
            except Exception as e:
                print(f"{device_label} Failed to subscribe SYNC_TX: {e}")

            # Handshake: write RIDE_ON to SYNC_RX (write without response)
            try:
                await client.write_gatt_char(ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, RIDE_ON, response=False)
                print(f"{device_label} Sent handshake: RIDE_ON")
            except Exception as e:
                print(f"{device_label} Failed to write handshake: {e}")

            # Listen a while
            try:
                await asyncio.sleep(RUN_SECONDS)
            finally:
                try:
                    await client.stop_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                try:
                    await client.stop_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                print(f"{device_label} Stopped listening. Disconnecting.")
    except Exception as e:
        print(f"{device_label} Error: {e}")


async def start_reader():
    """Scan for devices and connect to all of them concurrently."""
    devices = await find_devices()
    if not devices:
        return

    # Create concurrent tasks for all devices
    tasks = [connect_to_controller(device, i) for i, device in enumerate(devices)]
    
    print(f"\nStarting to listen to {len(tasks)} controller(s) for {RUN_SECONDS} seconds...\n")
    
    # Run all connections concurrently
    await asyncio.gather(*tasks, return_exceptions=True)
    
    print("\nAll controllers disconnected.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("Warning: This script is optimized for macOS; Bleak works cross-platform, but Zwift Play pairing differs.")
    # Optional: pass address as first argument, or a custom name substring
    # Examples:
    #   python3 Zwift_Play_Reader.py AA:BB:CC:DD:EE:FF
    #   python3 Zwift_Play_Reader.py macos-uuid-like
    #   python3 Zwift_Play_Reader.py --name "Zwift Play"
    if len(sys.argv) > 1:
        if sys.argv[1] == "--name" and len(sys.argv) > 2:
            TARGET_NAME_SUBSTR = sys.argv[2]
            TARGET_ADDRESS = None
            print(f"Using name filter: {TARGET_NAME_SUBSTR}")
        else:
            TARGET_ADDRESS = sys.argv[1]
            print(f"Using explicit address: {TARGET_ADDRESS}")
    asyncio.run(start_reader())
