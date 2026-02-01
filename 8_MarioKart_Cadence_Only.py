import asyncio
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

from csc_cadence_sensor import CSC_MEASUREMENT_UUID, CSCCadenceCalculator, parse_csc_measurement
from overlay_udp import OverlayClient, OverlayConfig
from zwift_play_to_keyboard import run_zwift_play_mapper_forever

DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"

# Optional: Run Zwift Play controller mapping inside this script (so you only run one script).
ENABLE_ZWIFT_PLAY_CONTROLLERS = True

# Optional: Map cadence to keyboard keys (for games) while only reading cadence (no ERG control).
# Note: your game window must be focused for key presses to work.
ENABLE_KEY_MAPPING = True

# Default Mario Kart-style mapping (same idea as scripts 2–6)
Upper_CADENCE_THRESHOLD = 65.0
Lower_CADENCE_THRESHOLD = 30.0
Boost_CADENCE_THRESHOLD = 100.0

# Optional: Use an external cadence sensor (e.g., Garmin Cadence Sensor 2) instead of trainer cadence.
USE_EXTERNAL_CADENCE_SENSOR = True
CADENCE_SENSOR_ADDRESS = "5AAA25D1-0D9E-C93A-F661-6AC44233763A"
CADENCE_SENSOR_STALE_SECONDS = 3.0
CADENCE_SENSOR_RECONNECT_INTERVAL_SECONDS = 5.0

# Some trainers/sensors keep reporting the last cadence while coasting.
# If power is near zero, treat cadence as zero.
TRAINER_ZERO_CADENCE_POWER_WATTS = 5.0

# Optional: Show an always-on-top cadence overlay in the top-left corner.
# Note: If your game is in exclusive fullscreen, the overlay may not appear.
# Use borderless/windowed fullscreen for best results.
ENABLE_CADENCE_OVERLAY = False
OVERLAY_AUTOSTART = True
OVERLAY_PORT = 49555

_cadence_calc = CSCCadenceCalculator(stale_seconds=CADENCE_SENSOR_STALE_SECONDS)
_overlay = OverlayClient(
    OverlayConfig(
        enabled=ENABLE_CADENCE_OVERLAY,
        autostart=OVERLAY_AUTOSTART,
        port=OVERLAY_PORT,
        x=10,
        y=10,
        font=18,
        alpha=0.85,
    )
)

keyboard = Controller()
current_keys_pressed = set()

_KEY_DISPLAY_NAMES = {
    Key.up: "Up",
    Key.down: "Down",
    Key.left: "Left",
    Key.right: "Right",
    Key.shift: "Shift",
}


def format_pressed_keys(keys) -> str:
    if not keys:
        return "-"

    names = []
    for key in keys:
        if isinstance(key, Key):
            names.append(_KEY_DISPLAY_NAMES.get(key, str(key)))
        else:
            names.append(str(key).upper())

    return "+".join(sorted(names))


def apply_key_mapping(cadence: float):
    if not ENABLE_KEY_MAPPING:
        return

    keys_needed = set()
    if cadence > Boost_CADENCE_THRESHOLD:
        keys_needed.add("a")
        keys_needed.add(Key.up)
    elif cadence > Upper_CADENCE_THRESHOLD:
        keys_needed.add("a")
    elif cadence < Lower_CADENCE_THRESHOLD:
        keys_needed.add("b")

    global current_keys_pressed

    for key in current_keys_pressed - keys_needed:
        keyboard.release(key)

    for key in keys_needed - current_keys_pressed:
        keyboard.press(key)

    current_keys_pressed = keys_needed


async def run(address: str):
    async with BleakClient(address) as trainer_client:
        trainer = FitnessMachineService(trainer_client)

        _overlay.start()

        zwift_task = None
        if ENABLE_ZWIFT_PLAY_CONTROLLERS:
            zwift_task = asyncio.create_task(
                run_zwift_play_mapper_forever(rescan_interval_seconds=5.0),
                name="zwift-play-mapper",
            )
            print("Zwift Play mapper started (integrated).")

        cadence_client = None
        cadence_notify_active = False
        last_reconnect_attempt = 0.0

        def _on_csc(sender: int, payload: bytearray):
            crank_sample, _, _ = parse_csc_measurement(bytes(payload))
            if crank_sample is not None:
                _cadence_calc.update_from_crank_sample(crank_sample)

        async def _ensure_cadence_sensor_connected():
            nonlocal cadence_client, cadence_notify_active, last_reconnect_attempt

            if not USE_EXTERNAL_CADENCE_SENSOR:
                return
            if not CADENCE_SENSOR_ADDRESS.strip():
                return

            now = asyncio.get_event_loop().time()
            if cadence_client is not None and cadence_client.is_connected and cadence_notify_active:
                return

            if (now - last_reconnect_attempt) < CADENCE_SENSOR_RECONNECT_INTERVAL_SECONDS:
                return
            last_reconnect_attempt = now

            try:
                if cadence_client is None:
                    cadence_client = BleakClient(CADENCE_SENSOR_ADDRESS.strip())

                if not cadence_client.is_connected:
                    await cadence_client.connect()
                    cadence_notify_active = False

                if not cadence_notify_active:
                    await cadence_client.start_notify(CSC_MEASUREMENT_UUID, _on_csc)
                    cadence_notify_active = True
                    print(f"External cadence sensor connected: {CADENCE_SENSOR_ADDRESS.strip()}")
            except Exception:
                cadence_notify_active = False
                try:
                    if cadence_client is not None:
                        await cadence_client.disconnect()
                except Exception:
                    pass

        def _trainer_data_handler(data):
            power = data.instant_power or 0
            trainer_cadence = data.instant_cadence or 0
            if power <= TRAINER_ZERO_CADENCE_POWER_WATTS:
                trainer_cadence = 0

            if USE_EXTERNAL_CADENCE_SENSOR and _cadence_calc.is_fresh:
                cadence = _cadence_calc.cadence_rpm_last
                cadence_source = "Garmin"
            else:
                cadence = trainer_cadence
                cadence_source = "Trainer"

            speed = data.instant_speed or 0

            _overlay.send(cadence, cadence_source)

            apply_key_mapping(cadence)
            keys_str = format_pressed_keys(current_keys_pressed)

            print(
                f"Cadence: {cadence:5.1f} RPM ({cadence_source}) | "
                f"Power: {power:3.0f} W | "
                f"Speed: {speed:4.1f} km/h | "
                f"Keys: {keys_str}"
            )

        trainer.set_indoor_bike_data_handler(_trainer_data_handler)
        await trainer.enable_indoor_bike_data_notify()

        print("Connected. Reading cadence only (no ERG control). Press Ctrl+C to stop.")

        try:
            while True:
                await _ensure_cadence_sensor_connected()
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping…")
        finally:
            for key in current_keys_pressed:
                keyboard.release(key)

            if zwift_task is not None:
                zwift_task.cancel()
                try:
                    await asyncio.wait_for(zwift_task, timeout=2.0)
                except Exception:
                    pass

            if cadence_client is not None:
                try:
                    if cadence_client.is_connected and cadence_notify_active:
                        await cadence_client.stop_notify(CSC_MEASUREMENT_UUID)
                except Exception:
                    pass
                try:
                    await cadence_client.disconnect()
                except Exception:
                    pass

            _overlay.close()


if __name__ == "__main__":
    asyncio.run(run(DEVICE_ADDRESS))
