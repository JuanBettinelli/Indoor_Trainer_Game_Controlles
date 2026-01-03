This Repo is a small collection of scripts to play emulated games with an indoor trainer and a Zwift Play Controller.

- It is set up for a MAC computer, a Wahoo Kickr Core (https://eu.wahoofitness.com/devices/indoor-cycling/bike-trainers/kickr-core-2-buy), and a Zwift Play Controller (https://eu.zwift.com/de/products/zwift-play?variant=44565285994747). but can be easyly addapted for PC and Linux and other smart trainer or dumb trainer with speed/cadance sensors.

- For steering, a mobile phone attached to the handlebars can also be used. For this, use "Remote Gamepad" (https://remotegamepad.com)or a similar app and configure the tilt as steering input (and buttons).

- To set up, run the "0_Read_Bluetooth_Devices.py" script and copy the relevant address. (e.g. KICKR CORE 5836: D181282F-9CD3-AF69-9E8B-1A8113A614E6) only the last section of the output is needed.
Add the adress to the control scripts [2-7]. (DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6")

- To play: run the script "1_MarioKart_ZwiftPlay_to_Keyboard.py" and one of scripts [2-7]. They need to run in separate terminal windows. (python python 1_MarioKart_ZwiftPlay_to_Keyboard.py) & (python 2_MarioKart_ERG_mode_Constat_Power.py)

- Run game (eg. Mario Kart for Wii, https://apunkagames.click/cdromance/) in an emulator (e.g.  Dolphin, https://dolphin-emu.org). 

- In the emulator Edit the controller button layout us, use the screenshot to use the button layout as in the scripts. Use the "Classic Controler" extension for the button layout.

- The button layouts can also be edited in the scripts:
KEY_MAPPING_LEFT = {
    "Y": "up",                   # Up button -> Up arrow
    "Z": "left",                 # Left button -> Left arrow
    "A": "right",                # Right button -> Right arrow
    "B": "down",                 # Down button -> Down arrow
    "Side": "q",                 # Shift/Side button -> Q key
    "On/Off": "escape",          # On/Off button -> Escape key
    "right_paddle": "left",      # Right paddle -> Left key
    "left_paddle": "left",       # Left paddle -> Left key
}

KEY_MAPPING_RIGHT = {
    "Y": "x",                   # Y button -> X key
    "Z": "y",                   # Z button -> Y key
    "A": "a",                   # A button -> A key
    "B": "b",                   # B button -> B key
    "Side": "e",                # Shift/Side button -> E key
    "On/Off": "enter",          # On/Off button -> Enter key
    "right_paddle": "right",    # Right paddle -> Right key
    "left_paddle": "right",     # Left paddle -> Right key
}
and 
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

- When running the scipts it askes for power setting in Watts, if nothing is entered it uses defold values.

- Play!
