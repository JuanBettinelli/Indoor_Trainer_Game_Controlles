import asyncio
import threading
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"


def get_target_power_with_timeout(timeout=10, default=260):
    """Get target power from user input with timeout.
    
    Args:
        timeout: Time in seconds to wait for user input (default: 10)
        default: Default value if input is invalid or times out (default: 260)
    
    Returns:
        Integer value for target power in watts
    """
    result = [default]  # Use list to allow modification in thread
    
    def get_input():
        try:
            user_input = input(f"Enter HIIT target power in watts (default: {default}): ").strip()
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


HIGH_POWER_WATTS = get_target_power_with_timeout()
LOW_POWER_WATTS = int(HIGH_POWER_WATTS * 0.5)  # Low power is 50% of high
Upper_CADENCE_THRESHOLD = 65.0
Lower_CADENCE_THRESHOLD = 30.0
Boost_CADENCE_THRESHOLD = 100.0

# Pyramid interval tracking
TARGET_POWER_WATTS = LOW_POWER_WATTS
cycle_start_time = None
CYCLE_DURATION = 120  # seconds (2 minutes for full cycle)
RAMP_UP_DURATION = 60  # seconds (half cycle to go from low to high)
RAMP_DOWN_DURATION = 60  # seconds (half cycle to go from high to low)
pyramid_phase = "up"  # "up" or "down"

keyboard = Controller()
current_keys_pressed = set()


# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
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

    # Add pyramid phase indicator
    phase_indicator = colorize(f"[{pyramid_phase.upper()}]", Colors.RED if pyramid_phase == "up" else Colors.BLUE)
    
    print(
        f"{phase_indicator} ERG target {TARGET_POWER_WATTS} W | "
        f"Power: {power} W | "
        f"{cadence_str} | "
        f"Speed: {speed:.1f} km/h"
    )


async def update_pyramid_power():
    """Update power linearly based on pyramid phase."""
    global pyramid_phase, cycle_start_time, TARGET_POWER_WATTS
    
    elapsed = asyncio.get_event_loop().time() - cycle_start_time
    
    if pyramid_phase == "up":
        # Ramp up from low to high
        progress = elapsed / RAMP_UP_DURATION
        if progress >= 1.0:
            # Switch to ramp down phase
            pyramid_phase = "down"
            cycle_start_time = asyncio.get_event_loop().time()
            TARGET_POWER_WATTS = HIGH_POWER_WATTS
            print(f"\n→ Peak reached! Now ramping down...\n")
        else:
            # Linear interpolation from low to high
            TARGET_POWER_WATTS = int(LOW_POWER_WATTS + (HIGH_POWER_WATTS - LOW_POWER_WATTS) * progress)
    else:  # down
        # Ramp down from high to low
        progress = elapsed / RAMP_DOWN_DURATION
        if progress >= 1.0:
            # Switch back to ramp up phase
            pyramid_phase = "up"
            cycle_start_time = asyncio.get_event_loop().time()
            TARGET_POWER_WATTS = LOW_POWER_WATTS
            print(f"\n→ Cycle complete! Starting new pyramid...\n")
        else:
            # Linear interpolation from high to low
            TARGET_POWER_WATTS = int(HIGH_POWER_WATTS - (HIGH_POWER_WATTS - LOW_POWER_WATTS) * progress)


async def run(address: str):
    global cycle_start_time
    
    async with BleakClient(address) as client:
        trainer = FitnessMachineService(client)

        trainer.set_indoor_bike_data_handler(log_bike_data)
        await trainer.enable_indoor_bike_data_notify()

        cycle_start_time = asyncio.get_event_loop().time()
        await trainer.set_target_power(TARGET_POWER_WATTS)
        print(f"Pyramid mode: LOW {LOW_POWER_WATTS} W → HIGH {HIGH_POWER_WATTS} W → LOW {LOW_POWER_WATTS} W")
        print(f"Cycle duration: {CYCLE_DURATION} seconds (ramp up {RAMP_UP_DURATION}s, ramp down {RAMP_DOWN_DURATION}s)")
        print(f"Key mapping: cadence > {Boost_CADENCE_THRESHOLD} RPM -> 'A' + 'Up', \n cadence > {Upper_CADENCE_THRESHOLD} RPM -> 'A', \n cadence < {Lower_CADENCE_THRESHOLD} RPM -> 'B'")
        print(f"\nStarting at LOW power: {TARGET_POWER_WATTS} W. Start pedaling.\n")

        try:
            while True:
                await update_pyramid_power()
                await trainer.set_target_power(TARGET_POWER_WATTS)
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping ERG session…")
        finally:
            for key in current_keys_pressed:
                keyboard.release(key)


if __name__ == "__main__":
    asyncio.run(run(DEVICE_ADDRESS))
