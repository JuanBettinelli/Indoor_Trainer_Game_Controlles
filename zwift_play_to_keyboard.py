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
from typing import Dict, Tuple, List, Optional, Set, Any, Callable

from bleak import BleakClient, BleakScanner
from pynput.keyboard import Controller, Key

from controller_config import mappings as load_controller_mappings, normalize_key_name, vibration_enabled
from runtime_devices import addresses_for

# Zwift UUIDs extracted from swiftcontrol (BikeControl)
ZWIFT_CUSTOM_SERVICE_UUID = "00000001-19CA-4651-86E5-FA29DCDD09D1"
ZWIFT_ASYNC_CHARACTERISTIC_UUID = "00000002-19CA-4651-86E5-FA29DCDD09D1"  # notify
ZWIFT_SYNC_RX_CHARACTERISTIC_UUID = "00000003-19CA-4651-86E5-FA29DCDD09D1"  # write w/o response
ZWIFT_SYNC_TX_CHARACTERISTIC_UUID = "00000004-19CA-4651-86E5-FA29DCDD09D1"  # indicate/notify

# Message types (per swiftcontrol constants)
PLAY_NOTIFICATION_MESSAGE_TYPE = 0x07
CLICK_NOTIFICATION_MESSAGE_TYPE = 0x37
BATTERY_LEVEL_TYPE = 0x19

# Handshake bytes (per swiftcontrol constants)
RIDE_ON = bytes([0x52, 0x69, 0x64, 0x65, 0x4F, 0x6E])  # "RideOn"
VIBRATE_COMMAND = bytes([0x12, 0x12, 0x08, 0x0A, 0x06, 0x08, 0x02, 0x10, 0x00, 0x18, 0x20])

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

# Loaded from controller_mapping.json, with built-in defaults before setup is run.
KEY_MAPPING_LEFT, KEY_MAPPING_RIGHT, KEY_MAPPING_CLICK = load_controller_mappings()


class ZwiftHapticHub:
    """Send haptic feedback to every connected Zwift Play controller."""

    def __init__(self, enabled: Optional[bool] = None):
        self.enabled = vibration_enabled() if enabled is None else enabled
        self._clients: dict[str, BleakClient] = {}
        self._write_lock = asyncio.Lock()

    def register(self, label: str, client: BleakClient) -> None:
        self._clients[label] = client

    def unregister(self, label: str) -> None:
        self._clients.pop(label, None)

    async def pulse(self, count: int = 1, gap_seconds: float = 0.16) -> bool:
        if not self.enabled or not self._clients:
            return False
        async with self._write_lock:
            delivered = False
            for pulse_index in range(max(1, count)):
                delivered = await self._write_pulse() or delivered
                if pulse_index + 1 < count:
                    await asyncio.sleep(gap_seconds)
            return delivered

    async def sustained(self, duration_seconds: float = 1.0, repeat_seconds: float = 0.12) -> bool:
        """Approximate a continuous vibration by refreshing the Play haptic command."""
        if not self.enabled or not self._clients:
            return False
        async with self._write_lock:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(0.0, duration_seconds)
            delivered = False
            while loop.time() < deadline:
                delivered = await self._write_pulse() or delivered
                await asyncio.sleep(min(repeat_seconds, max(0.0, deadline - loop.time())))
            return delivered

    async def _write_pulse(self) -> bool:
        results = await asyncio.gather(
            *(
                client.write_gatt_char(ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, VIBRATE_COMMAND, response=False)
                for client in list(self._clients.values())
                if client.is_connected
            ),
            return_exceptions=True,
        )
        return any(not isinstance(result, Exception) for result in results)

# Global keyboard controller
keyboard = Controller()

# Track pressed keys per device to avoid duplicate presses
device_pressed_keys: Dict[str, Set[str]] = {}

# Persist inferred controller side per device label.
# Some notifications (notably analog/paddle updates) may omit FIELD_RIGHT_PAD,
# so re-inferring side on every packet can cause flapping.
device_side: Dict[str, str] = {}

# Paddle analog LR thresholds (zigzag-decoded signed int).
# Use hysteresis to keep holds stable when the analog value jitters.
PADDLE_ON_THRESHOLD = 80
PADDLE_OFF_THRESHOLD = 60


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


def decode_buttons(fields: Dict[int, int], *, side_override: Optional[str] = None) -> Dict[str, Any]:
    """Return a human-friendly dict with side + pressed buttons.

    Notes:
    - Some packets may omit FIELD_RIGHT_PAD, so caller may pass side_override.
    - Paddles are derived from analog LR; caller may add hysteresis.
    """
    if side_override is not None:
        side = side_override
    else:
        side = "right" if fields.get(FIELD_RIGHT_PAD, OFF) == ON else "left"

    pressed: List[str] = []

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

    return {"side": side, "buttons": pressed, "analog_lr": analog_lr}


def get_key_object(key_name: str):
    """Convert string key name to pynput Key object."""
    normalized = normalize_key_name(key_name)
    if hasattr(Key, normalized):
        return getattr(Key, normalized)
    return normalized


def get_key_mapping(
    side: str,
    key_mapping_left: Dict[str, str],
    key_mapping_right: Dict[str, str],
    key_mapping_click: Dict[str, str],
) -> Dict[str, str]:
    if side == "click":
        return key_mapping_click
    return key_mapping_left if side == "left" else key_mapping_right


def press_key(device_label: str, button_name: str, side: str, key_mapping_left: Dict[str, str], key_mapping_right: Dict[str, str], key_mapping_click: Dict[str, str]):
    mapping = get_key_mapping(side, key_mapping_left, key_mapping_right, key_mapping_click)
    if button_name not in mapping:
        return

    key_name = mapping[button_name]
    key_obj = get_key_object(key_name)

    device_pressed_keys.setdefault(device_label, set()).add(button_name)
    keyboard.press(key_obj)
    print(f"{device_label} [{side}] Press: {button_name} → {key_name}")


def release_key(device_label: str, button_name: str, side: str, key_mapping_left: Dict[str, str], key_mapping_right: Dict[str, str], key_mapping_click: Dict[str, str]):
    mapping = get_key_mapping(side, key_mapping_left, key_mapping_right, key_mapping_click)
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
    controller_type: str,
    key_mapping_left: Dict[str, str],
    key_mapping_right: Dict[str, str],
    key_mapping_click: Dict[str, str],
    on_click_power_delta: Optional[Callable[[int], Any]],
    haptic_hub: ZwiftHapticHub,
) -> None:
    """Connect to a controller and map buttons to keyboard."""
    # Include address to avoid collisions when multiple controllers share the same name.
    device_label = f"[{device.name or 'Unknown'} {device.address}]"
    device_pressed_keys[device_label] = set()

    print(f"{device_label} Connecting ({device.address})...")

    try:
        async with BleakClient(device) as client:
            battery_reported = False

            def _release_all_for_device():
                for button in list(device_pressed_keys.get(device_label, set())):
                    for side in ["left", "right", "click"]:
                        try:
                            release_key(device_label, button, side, key_mapping_left, key_mapping_right, key_mapping_click)
                        except Exception:
                            pass

            async def on_async(_, data: bytearray):
                nonlocal battery_reported
                b = bytes(data)
                if not b:
                    return

                msg_type = b[0]
                payload = b[1:]

                if msg_type == CLICK_NOTIFICATION_MESSAGE_TYPE and controller_type == "click":
                    try:
                        fields = parse_play_keypad_status(payload)
                        previous_buttons = device_pressed_keys.get(device_label, set()).copy()
                        current_buttons = set()
                        if fields.get(1) == ON:
                            current_buttons.add("Plus")
                        if fields.get(2) == ON:
                            current_buttons.add("Minus")
                        for button in previous_buttons - current_buttons:
                            if on_click_power_delta is None:
                                release_key(device_label, button, "click", key_mapping_left, key_mapping_right, key_mapping_click)
                        for button in current_buttons - previous_buttons:
                            if on_click_power_delta is not None:
                                adjustment_applied = on_click_power_delta(1 if button == "Plus" else -1)
                                if adjustment_applied:
                                    await haptic_hub.pulse()
                            else:
                                press_key(device_label, button, "click", key_mapping_left, key_mapping_right, key_mapping_click)
                        device_pressed_keys[device_label] = current_buttons
                    except Exception as e:
                        print(f"{device_label} Error handling Click notification: {e}; raw={payload.hex(' ')}")

                elif msg_type == PLAY_NOTIFICATION_MESSAGE_TYPE and controller_type == "play":
                    try:
                        fields = parse_play_keypad_status(payload)
                        # Persist side per device to avoid side flapping if FIELD_RIGHT_PAD is omitted.
                        side = device_side.get(device_label)
                        if FIELD_RIGHT_PAD in fields:
                            side = "right" if fields.get(FIELD_RIGHT_PAD, OFF) == ON else "left"
                            device_side[device_label] = side
                        if side is None:
                            side = "left"
                            device_side[device_label] = side

                        info = decode_buttons(fields, side_override=side)

                        previous_buttons = device_pressed_keys.get(device_label, set()).copy()
                        current_buttons = set(info["buttons"])

                        # Paddle handling via analog LR with hysteresis.
                        analog_lr = info.get("analog_lr")
                        if isinstance(analog_lr, int):
                            # Right paddle
                            if analog_lr >= PADDLE_ON_THRESHOLD:
                                current_buttons.add("right_paddle")
                            elif "right_paddle" in previous_buttons and analog_lr >= PADDLE_OFF_THRESHOLD:
                                current_buttons.add("right_paddle")

                            # Left paddle
                            if analog_lr <= -PADDLE_ON_THRESHOLD:
                                current_buttons.add("left_paddle")
                            elif "left_paddle" in previous_buttons and analog_lr <= -PADDLE_OFF_THRESHOLD:
                                current_buttons.add("left_paddle")

                        previous_buttons = device_pressed_keys.get(device_label, set()).copy()

                        for button in previous_buttons - current_buttons:
                            release_key(device_label, button, side, key_mapping_left, key_mapping_right, key_mapping_click)

                        for button in current_buttons - previous_buttons:
                            press_key(device_label, button, side, key_mapping_left, key_mapping_right, key_mapping_click)

                        device_pressed_keys[device_label] = current_buttons

                    except Exception as e:
                        print(f"{device_label} Error handling Play notification: {e}; raw={payload.hex(' ')}")

                elif msg_type == BATTERY_LEVEL_TYPE:
                    if len(payload) >= 2 and not battery_reported:
                        print(f"{device_label} Battery: {payload[1]}%")
                        battery_reported = True

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
                if controller_type == "play":
                    haptic_hub.register(device_label, client)
                print(f"{device_label} Ready.")
            except Exception as e:
                print(f"{device_label} Failed to write handshake: {e}")

            try:
                while True:
                    await asyncio.sleep(0.1)
            finally:
                haptic_hub.unregister(device_label)
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
    controller_type: str = "play",
    key_mapping_left: Optional[Dict[str, str]] = None,
    key_mapping_right: Optional[Dict[str, str]] = None,
    key_mapping_click: Optional[Dict[str, str]] = None,
    on_click_power_delta: Optional[Callable[[int], Any]] = None,
    haptic_hub: Optional[ZwiftHapticHub] = None,
) -> None:
    """Scan once and connect to discovered controllers (runs until disconnect/cancel)."""
    key_mapping_left = KEY_MAPPING_LEFT if key_mapping_left is None else key_mapping_left
    key_mapping_right = KEY_MAPPING_RIGHT if key_mapping_right is None else key_mapping_right
    key_mapping_click = KEY_MAPPING_CLICK if key_mapping_click is None else key_mapping_click
    haptic_hub = haptic_hub or ZwiftHapticHub()

    devices = await find_devices(
        target_name_substr=target_name_substr,
        target_address=target_address,
        scan_timeout=scan_timeout,
        max_controllers=max_controllers,
    )
    if not devices:
        return

    tasks = [
        connect_to_controller(
            device,
            controller_type=controller_type,
            key_mapping_left=key_mapping_left,
            key_mapping_right=key_mapping_right,
            key_mapping_click=key_mapping_click,
            on_click_power_delta=on_click_power_delta,
            haptic_hub=haptic_hub,
        )
        for device in devices
    ]

    await asyncio.gather(*tasks, return_exceptions=True)


async def run_zwift_play_mapper_forever(
    *,
    target_name_substr: str = "Zwift",
    target_address: Optional[str] = None,
    controller_type: Optional[str] = None,
    scan_timeout: float = 6.0,
    max_controllers: int = 2,
    rescan_interval_seconds: float = 5.0,
    key_mapping_left: Optional[Dict[str, str]] = None,
    key_mapping_right: Optional[Dict[str, str]] = None,
    key_mapping_click: Optional[Dict[str, str]] = None,
    on_click_power_delta: Optional[Callable[[int], Any]] = None,
    haptic_hub: Optional[ZwiftHapticHub] = None,
) -> None:
    """Continuously scan/connect so you can run this in the background."""
    haptic_hub = haptic_hub or ZwiftHapticHub()
    while True:
        try:
            configured_targets = addresses_for("zwift_play") if target_address is None else None
            if configured_targets is None:
                await start_controller_mapper(
                    target_name_substr=target_name_substr,
                    target_address=target_address,
                    scan_timeout=scan_timeout,
                    max_controllers=max_controllers,
                    controller_type=controller_type or "play",
                    key_mapping_left=key_mapping_left,
                    key_mapping_right=key_mapping_right,
                    key_mapping_click=key_mapping_click,
                    on_click_power_delta=on_click_power_delta,
                    haptic_hub=haptic_hub,
                )
            else:
                configured_clicks = addresses_for("zwift_click") or []
                targets = [("play", address) for address in configured_targets]
                targets.extend(("click", address) for address in configured_clicks)
                if not targets:
                    print("No Zwift Play or Zwift Click controllers configured in devices.json.")
                    await asyncio.sleep(rescan_interval_seconds)
                    continue
                await asyncio.gather(
                    *(
                        start_controller_mapper(
                            target_name_substr=target_name_substr,
                            target_address=address,
                            scan_timeout=scan_timeout,
                            max_controllers=1,
                            controller_type=controller_type,
                            key_mapping_left=key_mapping_left,
                            key_mapping_right=key_mapping_right,
                            key_mapping_click=key_mapping_click,
                            on_click_power_delta=on_click_power_delta,
                            haptic_hub=haptic_hub,
                        )
                        for controller_type, address in targets
                    ),
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Zwift Play mapper error: {e}")

        await asyncio.sleep(rescan_interval_seconds)
