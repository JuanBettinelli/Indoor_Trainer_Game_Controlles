import asyncio
from bleak import BleakClient
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

# Initialize keyboard controller
keyboard = Controller()

# State tracking
current_keys_pressed = set()

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
    async with BleakClient(address) as client:
        def trainer_data_handler(data):
            """Handle incoming trainer data and simulate keyboard input"""
            cadence = data.instant_cadence or 0
            power = data.instant_power or 0
            speed = data.instant_speed or 0
            
            print(f"Cadence: {cadence} RPM, Power: {power} W, Speed: {speed:.1f} km/h")
            
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

        trainer = FitnessMachineService(client)
        trainer.set_indoor_bike_data_handler(trainer_data_handler)
        await trainer.enable_indoor_bike_data_notify()
        
        # Keep running and processing data
        print("Trainer connected! Start pedaling to control the game.")
        print("Mapping: Cadence > 60 = 'W' key, Cadence 40-60 = 'S' key, Power > 200W = Shift")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            # Release all keys
            for key in current_keys_pressed:
                keyboard.release(key)


if __name__ == "__main__":
    device_address = "D181282F-9CD3-AF69-9E8B-1A8113A614E6"
    asyncio.run(run(device_address))
