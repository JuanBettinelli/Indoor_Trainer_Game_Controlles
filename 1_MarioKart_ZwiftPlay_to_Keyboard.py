"""
Zwift Play controller to keyboard mapper (macOS)

- Connects to multiple Zwift Play controllers
- Maps button presses to keyboard keys
- Supports custom key mappings
- Works with both controllers simultaneously

Run:
  python3 Zwift_Play_Keyboard_Controller.py

Customize the KEY_MAPPING dictionary to map buttons to your desired keys.
"""

import asyncio
import sys
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
EMPTY_MESSAGE_TYPE = 0x15
BATTERY_LEVEL_TYPE = 0x19

# Handshake bytes (per swiftcontrol constants)
RIDE_ON = bytes([0x52, 0x69, 0x64, 0x65, 0x4F, 0x6E])  # "RideOn"
RESPONSE_START_PLAY = bytes([0x01, 0x04])

# Discovery parameters
TARGET_NAME_SUBSTR = "Zwift"
TARGET_ADDRESS: Optional[str] = None
SCAN_TIMEOUT = 6.0
MAX_CONTROLLERS = 2

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

# ============================================================================
# CUSTOMIZE THIS MAPPING TO YOUR NEEDS
# ============================================================================
# Map button names to keyboard keys using pynput.keyboard.Key or strings
# You can have different mappings for LEFT and RIGHT controllers

# LEFT CONTROLLER mapping
KEY_MAPPING_LEFT = {
    "Y": "up",                   # Up button -> Up arrow
    "Z": "left",                 # Left button -> Left arrow
    "A": "right",                # Right button -> Right arrow
    "B": "down",                 # Down button -> Down arrow
    "Side": "q",             # Shift/Side button -> Q key
    "On/Off": "escape",          # On/Off button -> Escape key
    "right_paddle": "left",         # Right paddle -> Left key
    "left_paddle": "left",          # Left paddle -> Left key
}

# RIGHT CONTROLLER mapping (different from left if desired)
KEY_MAPPING_RIGHT = {
    "Y": "x",                   # Y button -> X key
    "Z": "y",                 # Z button -> Y key
    "A": "a",                # A button -> A key
    "B": "b",                 # B button -> B key
    "Side": "e",             # Shift/Side button -> E key
    "On/Off": "enter",          # On/Off button -> Enter key
    "right_paddle": "right",  # Right paddle -> Right key
    "left_paddle": "right",     # Left paddle -> Right key
}
# ============================================================================

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
            paddles.append("right_paddle" if side == "right" else "left_paddle")

    return {"side": side, "buttons": pressed, "paddles": paddles}


def get_key_object(key_name: str):
    """Convert string key name to pynput Key object."""
    if hasattr(Key, key_name):
        return getattr(Key, key_name)
    return key_name


def get_key_mapping(side: str) -> Dict[str, str]:
    """Get the key mapping for the specified side (left or right)."""
    if side == "left":
        return KEY_MAPPING_LEFT
    else:
        return KEY_MAPPING_RIGHT


def press_key(device_label: str, button_name: str, side: str):
    """Press a key mapped to a button."""
    mapping = get_key_mapping(side)
    if button_name not in mapping:
        return
    
    key_name = mapping[button_name]
    key_obj = get_key_object(key_name)
    
    device_pressed_keys.setdefault(device_label, set()).add(button_name)
    keyboard.press(key_obj)
    print(f"{device_label} [{side}] Press: {button_name} → {key_name}")


def release_key(device_label: str, button_name: str, side: str):
    """Release a key mapped to a button."""
    mapping = get_key_mapping(side)
    if button_name not in mapping:
        return
    
    key_name = mapping[button_name]
    key_obj = get_key_object(key_name)
    
    if device_label in device_pressed_keys:
        device_pressed_keys[device_label].discard(button_name)
    keyboard.release(key_obj)
    print(f"{device_label} [{side}] Release: {button_name} ← {key_name}")


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
        matches = matches[:MAX_CONTROLLERS]

    if not matches:
        print(f"No devices matching name substring '{TARGET_NAME_SUBSTR}'. Are they on and disconnected?")
        return []
    
    print(f"\nWill connect to {len(matches)} device(s):")
    for m in matches:
        print(f"  • {m.name} ({m.address})")
    return matches


async def connect_to_controller(device: object, device_id: int) -> None:
    """Connect to a single controller and map buttons to keyboard."""
    device_label = f"[{device.name or 'Unknown'}]"
    device_pressed_keys[device_label] = set()
    
    print(f"\n{device_label} Connecting ({device.address})...")
    try:
        async with BleakClient(device) as client:
            print(f"{device_label} Connected. Discovering services...")
            services = client.services
            if services is None:
                await client.get_services()
                services = client.services

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
                        side = info["side"]
                        
                        current_buttons = set(info["buttons"]) | set(info["paddles"])
                        previous_buttons = device_pressed_keys.get(device_label, set()).copy()
                        
                        # Release buttons that are no longer pressed
                        for button in previous_buttons - current_buttons:
                            release_key(device_label, button, side)
                        
                        # Press new buttons
                        for button in current_buttons - previous_buttons:
                            press_key(device_label, button, side)
                        
                        device_pressed_keys[device_label] = current_buttons
                        
                    except Exception as e:
                        print(f"{device_label} Error parsing PlayKeyPadStatus: {e}; raw={payload.hex(' ')}")
                
                elif msg_type == BATTERY_LEVEL_TYPE:
                    if len(payload) >= 2:
                        print(f"{device_label} Battery: {payload[1]}%")

            async def on_sync_tx(_, data: bytearray):
                # Handshake responses
                pass

            # Subscribe
            try:
                await client.start_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID, on_async)
                print(f"{device_label} Subscribed to button notifications")
            except Exception as e:
                print(f"{device_label} Failed to subscribe: {e}")

            try:
                await client.start_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID, on_sync_tx)
            except Exception as e:
                pass

            # Handshake
            try:
                await client.write_gatt_char(ZWIFT_SYNC_RX_CHARACTERISTIC_UUID, RIDE_ON, response=False)
                print(f"{device_label} Handshake sent. Ready to receive button inputs.")
            except Exception as e:
                print(f"{device_label} Failed to write handshake: {e}")

            # Keep connection alive
            try:
                while True:
                    await asyncio.sleep(0.1)
            except KeyboardInterrupt:
                pass
            finally:
                # Release any remaining keys
                for button in list(device_pressed_keys.get(device_label, set())):
                    # Release with both left and right to be safe
                    for side in ["left", "right"]:
                        try:
                            release_key(device_label, button, side)
                        except:
                            pass
                
                try:
                    await client.stop_notify(ZWIFT_ASYNC_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                try:
                    await client.stop_notify(ZWIFT_SYNC_TX_CHARACTERISTIC_UUID)
                except Exception:
                    pass
                print(f"{device_label} Disconnected.")
    except Exception as e:
        print(f"{device_label} Error: {e}")


async def start_controller_mapper():
    """Scan for devices and connect to all of them."""
    devices = await find_devices()
    if not devices:
        return

    print("\n" + "="*60)
    print("KEY MAPPING - LEFT CONTROLLER:")
    print("="*60)
    for button, key in KEY_MAPPING_LEFT.items():
        print(f"  {button:20} → {key}")
    print("\n" + "="*60)
    print("KEY MAPPING - RIGHT CONTROLLER:")
    print("="*60)
    for button, key in KEY_MAPPING_RIGHT.items():
        print(f"  {button:20} → {key}")
    print("="*60 + "\n")

    # Create concurrent tasks for all devices
    tasks = [connect_to_controller(device, i) for i, device in enumerate(devices)]
    
    print(f"Starting keyboard mapper for {len(tasks)} controller(s)...")
    print("Press Ctrl+C to stop.\n")
    
    # Run all connections concurrently
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("Warning: This script is optimized for macOS.")
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--name" and len(sys.argv) > 2:
            TARGET_NAME_SUBSTR = sys.argv[2]
            TARGET_ADDRESS = None
            print(f"Using name filter: {TARGET_NAME_SUBSTR}")
        else:
            TARGET_ADDRESS = sys.argv[1]
            print(f"Using explicit address: {TARGET_ADDRESS}")
    
    try:
        asyncio.run(start_controller_mapper())
    except KeyboardInterrupt:
        print("\nStopped.")
