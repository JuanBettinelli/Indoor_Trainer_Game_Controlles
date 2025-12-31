import asyncio
import threading
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"


def get_target_power_with_timeout(timeout=10, default=160):
    """Get target power from user input with timeout.
    
    Args:
        timeout: Time in seconds to wait for user input (default: 10)
        default: Default value if input is invalid or times out (default: 160)
    
    Returns:
        Integer value for target power in watts
    """
    result = [default]  # Use list to allow modification in thread
    
    def get_input():
        try:
            user_input = input(f"Enter target power in watts (default: {default}): ").strip()
            if user_input:
                result[0] = int(user_input)
        except ValueError:
            print(f"Invalid input. Using default: {default}W")
            result[0] = default
    
    thread = threading.Thread(target=get_input, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    
    if thread.is_alive():
        print(f"Input timeout. Using default: {default}W")
    
    return result[0]


TARGET_POWER_WATTS = get_target_power_with_timeout()
Upper_CADENCE_THRESHOLD = 65.0
Lower_CADENCE_THRESHOLD = 30.0
Boost_CADENCE_THRESHOLD = 100.0


keyboard = Controller()
current_keys_pressed = set()


# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'


def colorize(text: str, color: str) -> str:
    """Wrap text with ANSI color codes."""
    return f"{color}{text}{Colors.RESET}"


def apply_key_mapping(cadence: float):
    """Press keys based on cadence thresholds."""
    keys_needed = set()
    if cadence > Boost_CADENCE_THRESHOLD:
        # Above 90 RPM: press both 'a' and Up arrow
        keys_needed.add("a")
        keys_needed.add(Key.up)
    elif cadence > Upper_CADENCE_THRESHOLD:
        keys_needed.add("a")
    elif cadence < Lower_CADENCE_THRESHOLD:
        keys_needed.add("b")
    else:
        pass  # No keys needed

    global current_keys_pressed

    # Release keys no longer needed
    for key in current_keys_pressed - keys_needed:
        keyboard.release(key)

    # Press new keys
    for key in keys_needed - current_keys_pressed:
        keyboard.press(key)

    current_keys_pressed = keys_needed


def log_bike_data(data):
    cadence = data.instant_cadence or 0
    power = data.instant_power or 0
    speed = data.instant_speed or 0

    apply_key_mapping(cadence)

    # Colorize cadence based on thresholds
    if cadence > Boost_CADENCE_THRESHOLD:
        cadence_str = colorize(f"Cadence: {cadence:.1f} RPM [BOOST!]", Colors.YELLOW)
    elif cadence > Upper_CADENCE_THRESHOLD:
        cadence_str = colorize(f"Cadence: {cadence:.1f} RPM", Colors.GREEN)
    elif cadence < Lower_CADENCE_THRESHOLD:
        cadence_str = colorize(f"Cadence: {cadence:.1f} RPM", Colors.RED)
    else:
        cadence_str = f"Cadence: {cadence:.1f} RPM"

    print(
        f"ERG target {TARGET_POWER_WATTS} W | "
        f"Power: {power} W | "
        f"{cadence_str} | "
        f"Speed: {speed:.1f} km/h"
    )


async def run(address: str):
    async with BleakClient(address) as client:
        trainer = FitnessMachineService(client)

        trainer.set_indoor_bike_data_handler(log_bike_data)
        await trainer.enable_indoor_bike_data_notify()

        await trainer.set_target_power(TARGET_POWER_WATTS)
        print(f"Set ERG mode to {TARGET_POWER_WATTS} W. Start pedaling.")
        print(f"Key mapping: cadence > {Boost_CADENCE_THRESHOLD} RPM -> 'A' + 'Up', \n cadence > {Upper_CADENCE_THRESHOLD} RPM -> 'A', \n cadence < {Lower_CADENCE_THRESHOLD} RPM -> 'B'")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping ERG session…")
        finally:
            for key in current_keys_pressed:
                keyboard.release(key)


if __name__ == "__main__":
    asyncio.run(run(DEVICE_ADDRESS))
