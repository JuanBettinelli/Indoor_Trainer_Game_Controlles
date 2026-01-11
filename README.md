This repo is a small collection of scripts for playing emulated games with an indoor trainer and a Zwift Play Controller, in particular Mario Kart Wii.
	•	It is set up for a Mac computer, a Wahoo Kickr Core (https://eu.wahoofitness.com/devices/indoor-cycling/bike-trainers/kickr-core-2-buy), and a Zwift Play Controller (https://eu.zwift.com/de/products/zwift-play?variant=44565285994747), but it should also work on PC and Linux (some adaptations for terminal color output may be required) and with other smart trainers or dumb trainers with speed/cadence sensors.

⸻

Small manual to get it running
	•	Install a retro game emulator (e.g. Dolphin, https://dolphin-emu.org). Others are also available, and PC games can also be used.
	•	Download the game/ROM (e.g. Mario Kart Wii, https://apunkagames.click/cdromance/).
	•	Run the game in the emulator.
	•	In the emulator, edit the controller button layout. Use the screenshot in the repo to match the button layout used in the scripts, optimized for Mario Kart. Use the “Classic Controller” extension controller configuration.

⸻

Python setup
	•	Install Python: download and install it from
https://www.python.org/downloads/
(important: check “Add to PATH” during installation)
	•	Open a terminal on Mac or Linux. On Windows, type “cmd” into the Windows search bar.
	•	In the terminal, type
python --version
and press Enter.
If an error appears, ask ChatGPT.
	•	In the terminal, type
pip install bleak pynput pycycling
and press Enter.

⸻

Trainer setup
	•	In the terminal, type python  (with a trailing space), then drag the file 0_Read_Bluetooth_Devices.py into the terminal and press Enter.
	•	The terminal should display output. Copy the device address (the long number after Wahoo, Tacx, etc., e.g.
DEVICE_ADDRESS = "D181282F-9CD3-AF69-9E8B-1A8113A614E6").
	•	Open the file 2_MarioKart_ERG_mode_Constat_Power.py with Notepad, VS Code, Python Launcher, etc., and replace the device address on line 7 with the copied address. Save and close the file.
	•	Open a new terminal (Windows: cmd) and again type python  (with a trailing space), then drag 2_MarioKart_ERG_mode_Constat_Power.py into the terminal and press Enter.
	•	Enter the watts you want to ride. If nothing is entered, the default is 160 W.
	•	Click on the Mario Kart window. Pedaling should now control the throttle.
Key mapping:
	•	cadence > 100.0 RPM → 'A' + 'Up'
	•	cadence > 65.0 RPM and ≤ 100.0 RPM → 'A'
	•	cadence > 30.0 RPM and ≤ 60.0 RPM → ''
	•	cadence < 30.0 RPM → 'B'
This can be changed in the script.
	•	Scripts 3 to 7 provide different variable power profiles, but work very similarly to script 2.

⸻

Steering with Zwift Play Controllers
	•	Power on the controllers.
	•	Open a new terminal (Windows: cmd) and type python  (with a trailing space), then drag 1_MarioKart_ZwiftPlay_to_Keyboard.py into the terminal and press Enter.
	•	The script will find the controllers automatically. Click on the game (Mario Kart) window. The controllers should now control the game.
	•	Phone alternative: A mobile phone mounted on the handlebars can also be used. Use Remote Gamepad (https://remotegamepad.com) or a similar app and configure tilt as steering input (and buttons).

⸻

Button layout (editable in the scripts)
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


To play
	•	The emulator with the game must be running.
	•	1_MarioKart_ZwiftPlay_to_Keyboard.py must be running in one terminal.
	•	2_MarioKart_ERG_mode_Constat_Power.py (or scripts 3–7) must be running in another terminal.
	•	Click on the game window to give it focus.