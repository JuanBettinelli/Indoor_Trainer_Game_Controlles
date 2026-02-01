"""Zwift Play controller -> keyboard mapper (reusable module).

This is a refactor of 1_MarioKart_ZwiftPlay_to_Keyboard.py so other scripts can
import and run it as a background task.

Typical usage from another async script:

    from zwift_play_to_keyboard import run_zwift_play_mapper_forever

    task = asyncio.create_task(run_zwift_play_mapper_forever())

The mapper will:
- scan for up to 2 Zwift Play controllers
- connect and map button notifications to keyboard presses
- if controllers disconnect / are not found, keep retrying periodically
"""

import asyncio
from typing import Dict, Tuple, List, Optional, Set

from bleak import BleakClient, BleakScanner
from pynput.keyboard import Controller, Key

# Zwift UUIDs extracted from swiftcontrol (BikeControl)
ZWIFT_CUSTOM_SERVICE_UUID = "00000001-19CA-4651-86E5-FA29DCDD09D1"
ZWIFT_ASYNC_CHARACTERISTIC_UUID = "00000002-19CA-4651-86E5-FA29DCDD09D1"  # notify
ZWIFT_SYNC_RX_CHARACTERISTIC_UUID = "00000003-19CA-4651-86E5-FA29DCDD09D1"  # write w/o response
ZWIFT_SYNC_TX_CHARACTERISTIC_UUID = "00000004-19CA-4651-86E5-FA29DCDD09D1"  # indicate/notify

# Message types (per swiftcontrol constants)
PLAY_NOTIFICATION_MESSAGE_TYPE = 0x07
BATTERY_LEVEL_TYPE = 0x19

# Handshake bytes (per swiftcontrol constants)
RIDE_ON = bytes([0x52, 0x69, 0x64, 0x65, 0x4F, 0x6E])  # "RideOn"

# PlayKeyPadStatus field numbers (protobuf varint)
FIELD_RIGHT_PAD = 1  # 0=ON (right), 1=OFF (left)
FIELD_Y_UP = 2       # 0=ON pressed
FIELD_Z_LEFT = 3
FIELD_A_RIGHT = 4
FIELD_B_DOWN = 5
FIELD_SHIFT = 6
FIELD_ON = 7
FIELD_ANALOG_LR = 8  # signed varint (zigzag)

# Enum mapping: ON=0, OFF=1 per swiftcontrol PlayButtonStatus
ON = 0
OFF = 1

# ============================================================================
# DEFAULT MAPPINGS (same as original script)
# ============================================================================
KEY_MAPPING_LEFT = {
    "Y": "up",
    "Z": "left",
    "A": "right",
    "B": "down",
    "Side": "q",
    "On/Off": "escape",
    "right_paddle": "left",
    "left_paddle": "left",
}

KEY_MAPPING_RIGHT = {
    "Y": "x",
    "Z": "y",
    "A": "a",
    "B": "b",
    "Side": "e",
    "On/Off": "enter",
    "right_paddle": "right",
    "left_paddle": "right",
}

# Global keyboard controller
keyboard = Controller()

# Track pressed keys per device to avoid duplicate presses
device_pressed_keys: Dict[str, Set[str]] = {}


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
            raise ValueError(f"Unexpected wire type {wire_type} for field {field_number}")
        val, i = _read_varint(message, i)
        fields[field_number] = val
    return fields


def decode_buttons(fields: Dict[int, int]) -> Dict[str, List[str]]:
    """Return a human-friendly dict with side + pressed buttons."""
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

    analog_lr = fields.get(FIELD_ANALOG_LR)
    if analog_lr is not None:
        analog_lr = _zigzag_decode(analog_lr)
        if abs(analog_lr) >= 100:
            paddles.append("right_paddle" if side == "right" else "left_paddle")

    return {"side": side, "buttons": pressed, "paddles": paddles}


def get_key_object(key_name: str):
    """Convert string key name to pynput Key object."""
    if hasattr(Key, key_name):
        return getattr(Key, key_name)
    return key_name


def get_key_mapping(side: str, key_mapping_left: Dict[str, str], key_mapping_right: Dict[str, str]) -> Dict[str, str]:
    return key_mapping_left if side == "left" else key_mapping_right


def press_key(device_label: str, button_name: str, side: str, key_mapping_left: Dict[str, str], key_mapping_right: Dict[str, str]):
    mapping = get_key_mapping(side, key_mapping_left, key_mapping_right)
    if button_name not in mapping:
        return

    key_name = mapping[button_name]
    key_obj = get_key_object(key_name)

    device_pressed_keys.setdefault(device_label, set()).add(button_name)
    keyboard.press(key_obj)
    print(f"{device_label} [{side}] Press: {button_name} → {key_name}")


def release_key(device_label: str, button_name: str, side: str, key_mapping_left: Dict[str, str], key_mapping_right: Dict[str, str]):
    mapping = get_key_mapping(side, key_mapping_left, key_mapping_right)
    if button_name not in mapping:
        return

    key_name = mapping[button_name]
    key_obj = get_key_object(key_name)

    if device_label in device_pressed_keys:
        device_pressed_keys[device_label].discard(button_name)
    keyboard.release(key_obj)
    print(f"{device_label} [{side}] Release: {button_name} ← {key_name}")


async def find_devices(
    *,
    target_name_substr: str = "Zwift",
    target_address: Optional[str] = None,
    scan_timeout: float = 6.0,
    max_controllers: int = 2,
) -> List[object]:
    """Find matching Zwift Play devices."""
    print(f"Scanning for Zwift Play BLE devices (timeout={scan_timeout}s)...")
    devices = await BleakScanner.discover(timeout=scan_timeout)

    matches: List[object] = []
    if target_address:
        match = next((d for d in devices if d.address == target_address), None)
        if match:
            matches.append(match)
        else:
            print(f"No device at address {target_address}")
    else:
        matches = [d for d in devices if d.name and target_name_substr.lower() in d.name.lower()]
        matches = matches[:max_controllers]

    if not matches:
        print(f"No Zwift Play devices found (filter='{target_name_substr}').")
        return []

    print(f"Will connect to {len(matches)} controller(s):")
    for m in matches:
        print(f"  • {m.name} ({m.address})")

    return matches


async def connect_to_controller(
    device: object,
    *,
    key_mapping_left: Dict[str, str],
    key_mapping_right: Dict[str, str],
) -> None:
    """Connect to a controller and map buttons to keyboard."""
    device_label = f"[{device.name or 'Unknown'}]"
    device_pressed_keys[device_label] = set()

    print(f"{device_label} Connecting ({device.address})...")

    try:
        async with BleakClient(device) as client:
            def _release_all_for_device():
                for button in list(device_pressed_keys.get(device_label, set())):
                    for side in ["left", "right"]:
                        try:
                            release_key(device_label, button, side, key_mapping_left, key_mapping_right)
                        except Exception:
                            pass

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
                        side = info["side"]

                        current_buttons = set(info["buttons"]) | set(info["paddles"])
                        previous_buttons = device_pressed_keys.get(device_label, set()).copy()

                        for button in previous_buttons - current_buttons:
                            release_key(device_label, button, side, key_mapping_left, key_mapping_right)

                        for button in current_buttons - previous_buttons:
                            press_key(device_label, button, side, key_mapping_left, key_mapping_right)

                        device_pressed_keys[device_label] = current_buttons

                    except Exception as e:
                        print(f"{device_label} Error parsing PlayKeyPadStatus: {e}; raw={payload.hex(' ')}")

                elif msg_type == BATTERY_LEVEL_TYPE:
                    if len(payload) >= 2:
                        print(f"{device_label} Battery: {payload[1]}%")

            async def on_sync_tx(_, __):
                pass

            try:
                await client.start_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID, on_async)
            except Exception as e:
                print(f"{device_label} Failed to subscribe (async notify): {e}")

            try:
                await client.start_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID, on_sync_tx)
            except Exception:
                pass

            try:
                await client.write_gatt_char(ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, RIDE_ON, response=False)
                print(f"{device_label} Ready.")
            except Exception as e:
                print(f"{device_label} Failed to write handshake: {e}")

            try:
                while True:
                    await asyncio.sleep(0.1)
            finally:
                _release_all_for_device()
                try:
                    await client.stop_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                try:
                    await client.stop_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                print(f"{device_label} Disconnected.")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"{device_label} Error: {e}")


async def start_controller_mapper(
    *,
    target_name_substr: str = "Zwift",
    target_address: Optional[str] = None,
    scan_timeout: float = 6.0,
    max_controllers: int = 2,
    key_mapping_left: Optional[Dict[str, str]] = None,
    key_mapping_right: Optional[Dict[str, str]] = None,
) -> None:
    """Scan once and connect to discovered controllers (runs until disconnect/cancel)."""
    key_mapping_left = key_mapping_left or KEY_MAPPING_LEFT
    key_mapping_right = key_mapping_right or KEY_MAPPING_RIGHT

    devices = await find_devices(
        target_name_substr=target_name_substr,
        target_address=target_address,
        scan_timeout=scan_timeout,
        max_controllers=max_controllers,
    )
    if not devices:
        return

    tasks = [
        connect_to_controller(device, key_mapping_left=key_mapping_left, key_mapping_right=key_mapping_right)
        for device in devices
    ]

    await asyncio.gather(*tasks, return_exceptions=True)


async def run_zwift_play_mapper_forever(
    *,
    target_name_substr: str = "Zwift",
    target_address: Optional[str] = None,
    scan_timeout: float = 6.0,
    max_controllers: int = 2,
    rescan_interval_seconds: float = 5.0,
    key_mapping_left: Optional[Dict[str, str]] = None,
    key_mapping_right: Optional[Dict[str, str]] = None,
) -> None:
    """Continuously scan/connect so you can run this in the background."""
    while True:
        try:
            await start_controller_mapper(
                target_name_substr=target_name_substr,
                target_address=target_address,
                scan_timeout=scan_timeout,
                max_controllers=max_controllers,
                key_mapping_left=key_mapping_left,
                key_mapping_right=key_mapping_right,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Zwift Play mapper error: {e}")

        await asyncio.sleep(rescan_interval_seconds)
