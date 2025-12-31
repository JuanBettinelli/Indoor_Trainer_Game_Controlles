import asyncio
import threading
import random
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"


def get_target_power_with_timeout(timeout=10, default=140):
    """Get target power from user input with timeout.
    
    Args:
        timeout: Time in seconds to wait for user input (default: 10)
        default: Default value if input is invalid or times out (default: 140)
    
    Returns:
        Integer value for target power in watts
    """
    result = [default]  # Use list to allow modification in thread
    
    def get_input():
        try:
            user_input = input(f"Enter base load power in watts (default: {default}): ").strip()
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


BASE_POWER_WATTS = get_target_power_with_timeout()
MAX_PEAK_POWER_MULTIPLIER = 3.0  # Peaks can go up to 3x base power
Upper_CADENCE_THRESHOLD = 65.0
Lower_CADENCE_THRESHOLD = 30.0
Boost_CADENCE_THRESHOLD = 90.0

# Chaos peaks tracking
TARGET_POWER_WATTS = BASE_POWER_WATTS
current_peak_power = None
current_peak_duration = None
peak_start_time = None
peak_active = False
next_peak_time = None

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

    # Add peak indicator
    if peak_active:
        peak_intensity = (TARGET_POWER_WATTS - BASE_POWER_WATTS) / (int(BASE_POWER_WATTS * MAX_PEAK_POWER_MULTIPLIER) - BASE_POWER_WATTS)
        if peak_intensity > 0.7:
            peak_indicator = colorize("[⚡ INTENSE PEAK!]", Colors.RED)
        else:
            peak_indicator = colorize("[⚡ peak]", Colors.YELLOW)
    else:
        peak_indicator = colorize("[base]", Colors.BLUE)
    
    print(
        f"{peak_indicator} ERG target {TARGET_POWER_WATTS} W | "
        f"Power: {power} W | "
        f"{cadence_str} | "
        f"Speed: {speed:.1f} km/h"
    )


async def generate_random_peak():
    """Generate a random peak with power and duration inversely related."""
    global current_peak_power, current_peak_duration, next_peak_time
    
    # Random peak power between 1.2x and 2.0x base power
    peak_multiplier = random.uniform(1.2, MAX_PEAK_POWER_MULTIPLIER)
    current_peak_power = int(BASE_POWER_WATTS * peak_multiplier)
    
    # Duration inversely related to power: higher power = shorter duration
    # Min 3 sec, Max 15 sec
    # Normalize peak_multiplier to 0-1 range (1.2->0, 2.0->1)
    normalized = (peak_multiplier - 1.2) / (MAX_PEAK_POWER_MULTIPLIER - 1.2)
    # Inverse: 0->15 sec, 1->3 sec
    current_peak_duration = int(15 - (normalized * 12))
    current_peak_duration = max(3, min(15, current_peak_duration))
    
    next_peak_time = random.uniform(5, 20)  # Next peak in 5-20 seconds


async def update_chaos_peaks():
    """Update power based on random peaks."""
    global peak_active, peak_start_time, TARGET_POWER_WATTS, next_peak_time
    
    current_time = asyncio.get_event_loop().time()
    
    if peak_active:
        # Check if peak duration has elapsed
        elapsed = current_time - peak_start_time
        if elapsed >= current_peak_duration:
            # Peak is over, return to base
            peak_active = False
            TARGET_POWER_WATTS = BASE_POWER_WATTS
            print(f"\n↓ Peak over! Back to base {BASE_POWER_WATTS} W\n")
            await generate_random_peak()
            next_peak_time = current_time + next_peak_time
    else:
        # Check if it's time for next peak
        if next_peak_time is not None and current_time >= next_peak_time:
            peak_active = True
            peak_start_time = current_time
            TARGET_POWER_WATTS = current_peak_power
            print(f"\n⚡ PEAK! {current_peak_power} W for {current_peak_duration} seconds!\n")


async def run(address: str):
    global next_peak_time
    
    async with BleakClient(address) as client:
        trainer = FitnessMachineService(client)

        trainer.set_indoor_bike_data_handler(log_bike_data)
        await trainer.enable_indoor_bike_data_notify()

        # Generate first peak timing
        next_peak_time = asyncio.get_event_loop().time() + random.uniform(3, 10)
        await generate_random_peak()
        
        await trainer.set_target_power(TARGET_POWER_WATTS)
        print(f"⚡ Chaos Peaks mode: Random power spikes on {BASE_POWER_WATTS} W base")
        print(f"Peaks range: {int(BASE_POWER_WATTS * 1.2)} W to {int(BASE_POWER_WATTS * MAX_PEAK_POWER_MULTIPLIER)} W")
        print(f"Duration: Higher power = shorter peaks (3-15 seconds)")
        print(f"Key mapping: cadence > {Boost_CADENCE_THRESHOLD} RPM -> 'A' + 'Up', \n cadence > {Upper_CADENCE_THRESHOLD} RPM -> 'A', \n cadence < {Lower_CADENCE_THRESHOLD} RPM -> 'B'")
        print(f"\nStarting at base power: {TARGET_POWER_WATTS} W. Stay ready for peaks!\n")

        try:
            while True:
                await update_chaos_peaks()
                await trainer.set_target_power(TARGET_POWER_WATTS)
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping ERG session…")
        finally:
            for key in current_keys_pressed:
                keyboard.release(key)


if __name__ == "__main__":
    asyncio.run(run(DEVICE_ADDRESS))
