import asyncio
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

from csc_cadence_sensor import CSC_MEASUREMENT_UUID, CSCCadenceCalculator, parse_csc_measurement
from overlay_udp import OverlayClient, OverlayConfig
from runtime_devices import CADENCE_SENSOR_ADDRESS, INDOOR_TRAINER_ADDRESS
from zwift_play_to_keyboard import run_zwift_play_mapper_forever

# Initialize keyboard controller
keyboard = Controller()

# State tracking
current_keys_pressed = set()

# Optional: Run Zwift Play controller mapping inside this script (so you only run one script).
ENABLE_ZWIFT_PLAY_CONTROLLERS = True

# Optional: Use an external cadence sensor (e.g., Garmin Cadence Sensor 2) instead of trainer cadence.
USE_EXTERNAL_CADENCE_SENSOR = True
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

def map_cadence_to_keys(cadence, power):
    """
    Map trainer data to keyboard keys.
    
    Example mapping:
    - cadence > 60: Press 'w' (forward/accelerate)
    - cadence 40-60: Press 's' (slow/coast)
    - cadence < 40: Release all
    - power > 200: Press Shift (boost/sprint)
    """
    keys_to_press = set()
    
    if cadence > 60:
        keys_to_press.add('w')
    elif cadence > 40:
        keys_to_press.add('s')
    
    if power > 200:
        keys_to_press.add(Key.shift)
    
    return keys_to_press


async def run(address):
    async with BleakClient(address) as trainer_client:
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

        def trainer_data_handler(data):
            """Handle incoming trainer data and simulate keyboard input"""
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
            
            # Determine which keys should be pressed
            keys_needed = map_cadence_to_keys(cadence, power)
            
            global current_keys_pressed
            
            # Release keys that are no longer needed
            for key in current_keys_pressed - keys_needed:
                keyboard.release(key)
            
            # Press new keys
            for key in keys_needed - current_keys_pressed:
                keyboard.press(key)
            
            current_keys_pressed = keys_needed

            keys_str = format_pressed_keys(current_keys_pressed)
            print(
                f"Cadence: {cadence:5.1f} RPM ({cadence_source}) | "
                f"Power: {power:3.0f} W | "
                f"Speed: {speed:4.1f} km/h | "
                f"Keys: {keys_str}"
            )

        trainer = FitnessMachineService(trainer_client)
        trainer.set_indoor_bike_data_handler(trainer_data_handler)
        await trainer.enable_indoor_bike_data_notify()
        
        # Keep running and processing data
        print("Trainer connected! Start pedaling to control the game.")
        print("Mapping: Cadence > 60 = 'W' key, Cadence 40-60 = 'S' key, Power > 200W = Shift")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                await _ensure_cadence_sensor_connected()
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            # Release all keys
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
    asyncio.run(run(INDOOR_TRAINER_ADDRESS))
