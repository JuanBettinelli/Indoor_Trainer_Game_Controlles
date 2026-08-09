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

Bluetooth device setup
	•	Run `0_Setup_Devices.py` to scan for and assign devices once.
	•	The setup supports an indoor trainer, power meter, cadence sensor, heart-rate sensor, Zwift Play controllers, and Zwift Click controllers.
	•	First select every device you want to add or update from one common list. Then assign one or multiple types to each selected device.
	•	Detected service UUIDs and device names provide type suggestions, but you always make the final assignment.
	•	Selections are stored in the local `devices.json`. This file is intentionally excluded from Git because Bluetooth addresses are computer-specific.
	•	Run the setup again at any time to add or update devices. Unselected saved devices remain unchanged. Select a saved device and enter `-` at the type prompt to remove it from every role.

```sh
python 0_Setup_Devices.py
```

Show the saved configuration without scanning:

```sh
python 0_Setup_Devices.py --show
```

All runnable scripts now read their relevant devices from `devices.json`. This includes the indoor trainer, cadence sensor, saved Zwift Play controllers, and Zwift Click. Power meter, speed sensor, and heart-rate sensor can also be assigned for integrations that consume those data streams. Command-line options such as `--trainer` can still override a saved device where supported.

Configure controller buttons separately:

```sh
python 0_Setup_Controllers.py
```

This creates the local `controller_mapping.json`. You can configure the left and right Zwift Play mappings and the Zwift Click Plus/Minus mappings. Enter `-` for a button to disable it. All integrated controller scripts load this file automatically. To inspect the current mapping without changing it, run `python 0_Setup_Controllers.py --show`.

During ERG workouts (scripts 2–6 and 9), Zwift Click has a dedicated power function: `Plus` raises the smart-trainer target by 5 W and `Minus` lowers it by 5 W. This adjustment is applied as an offset to the current interval target, so it remains active when the workout moves to the next step. Outside an ERG workout, the normal keyboard mapping from `controller_mapping.json` is used.

When vibration feedback is enabled, a successful Zwift Click power adjustment produces one short pulse on both connected Play controllers. Before a new Intervals.icu workout step, the countdown is one pulse at 5 seconds, two pulses at 2 seconds, and three pulses at 1 second. The new step starts with an approximately one-second sustained vibration. There is no power-adjustment pulse when ERG is disabled or a safety limit prevents the target from changing. Enable or disable vibration with `python 0_Setup_Controllers.py`. To test the motors directly, close other apps using the controllers and run `python 0_Test_ZwiftPlay_Vibration.py`.

⸻

Optional: Garmin Cadence Sensor 2 (external cadence)
	•	If you have a separate cadence sensor (e.g. Garmin Cadence Sensor 2), you can use it as the cadence input instead of the trainer’s cadence.
	•	Run `python 0_Setup_Devices.py`, select the sensor, and assign it as `Cadence sensor`.
	•	The workout scripts use the saved address automatically when `USE_EXTERNAL_CADENCE_SENSOR = True`.
	•	Run the script normally. If the cadence sensor can’t connect, it will fall back to trainer cadence.

⸻

Trainer setup
	•	Run `python 0_Setup_Devices.py`, select your smart trainer, and assign it as `Indoor trainer`.
	•	The trainer scripts use the saved address automatically; no source-code edit is needed.
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
	•	If you want to ONLY read/display cadence (no ERG / no power control), run 8_Read_Cadence_Only.py.

⸻

Intervals.icu workouts in ERG mode (script 9)
	•	`9_MarioKart_Intervals_Workout.py` loads a planned cycling workout from Intervals.icu and runs its power prescription on the trainer.
	•	It uses the same Intervals.icu API key stored by the AI Coach tool in macOS Keychain. No API key is stored in this repository.
	•	Supported steps: timed fixed watts, timed FTP percentages, power ranges, linear ramps, cadence targets, repetitions, and `freeride`.
	•	Heart-rate-only, pace-only, zone-only, and distance-based steps are rejected because they cannot be converted safely into an ERG target.
	•	`freeride` resets trainer control for that step and restores ERG control at the next power-controlled step. This is suitable for a 20-minute FTP test.
	•	During the workout, script 9 records the indoor trainer, power meter, cadence sensor, speed sensor, and heart-rate monitor configured in `devices.json`.
	•	The finished ride is saved as a standard FIT activity in `recordings/YYYY-MM-DD/`. A matching `.raw.jsonl` sidecar preserves the complete Bluetooth measurements, including fields that FIT or Intervals.icu cannot represent.
	•	For FIT power and cadence, the power meter has priority. Separate cadence and speed sensors are used next, with the trainer as fallback. Heart rate comes from the configured heart-rate monitor.
	•	After the ride, the script asks before uploading the FIT activity to Intervals.icu. The planned workout is normally paired by its time and date.
	•	Press `Q` during a workout to finish it early (`F12` remains available as a fallback). The trainer is released safely, all collected data is written to FIT and raw JSONL files, and the normal upload prompt is shown.

Preview and start today's cycling workout:

```sh
python 9_MarioKart_Intervals_Workout.py --date today --ftp 280
```

Start without confirmation and upload automatically when the workout finishes:

```sh
python 9_MarioKart_Intervals_Workout.py --date today --ftp 280 --yes
```

Record locally without uploading:

```sh
python 9_MarioKart_Intervals_Workout.py --date today --ftp 280 --no-upload
```

The speed-sensor distance uses a 2105 mm wheel circumference by default. Override it when needed:

```sh
python 9_MarioKart_Intervals_Workout.py --wheel-circumference-mm 2130
```

Run a hardware-free, accelerated simulation first:

```sh
python 9_MarioKart_Intervals_Workout.py \
  --date today --ftp 280 --simulate --simulation-speed 120
```

Read an AI Coach plan file without contacting Intervals.icu:

```sh
python 9_MarioKart_Intervals_Workout.py \
  --date 2026-08-06 --ftp 280 \
  --local-plan /Users/juanbettinelli/Documents/AI_Coach/intervals_icu_tool/example_week.json \
  --simulate
```

The complete workout is resolved into watts and shown before any Bluetooth connection is opened. By default, targets above 600 W are rejected. Change the safety ceiling explicitly with `--max-watts` only after reviewing the preview.

⸻

Steering with Zwift Play Controllers
	•	Power on the controllers.
	•	If you run any of scripts 2–7, the Zwift Play mapping is integrated by default (ENABLE_ZWIFT_PLAY_CONTROLLERS = True), so you normally do NOT need to run a second script.
	•	Optional (controllers only): Open a new terminal (Windows: cmd) and type python  (with a trailing space), then drag 1_MarioKart_ZwiftPlay_to_Keyboard.py into the terminal and press Enter.
	•	The script will find the controllers automatically. Click on the game (Mario Kart) window. The controllers should now control the game.
	•	Phone alternative: A mobile phone mounted on the handlebars can also be used. Use Remote Gamepad (https://remotegamepad.com) or a similar app and configure tilt as steering input (and buttons).

⸻

Optional: On-screen cadence overlay (always on top)
	•	Scripts 2–7 can show a small always-on-top cadence overlay in the top-left corner.
	•	In the script you run, set:
		•	ENABLE_CADENCE_OVERLAY = True
		•	OVERLAY_AUTOSTART = True
	•	Tip: If your game is in exclusive fullscreen, the overlay may not appear. Borderless/windowed fullscreen works best.
	•	Press Escape while the overlay window is focused to close it.

⸻

Default button layout (editable with `0_Setup_Controllers.py`)
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
	•	Run ONE script: 2_MarioKart_ERG_mode_Constat_Power.py (or scripts 3–7).
	•	By default it will also connect Zwift Play controllers (ENABLE_ZWIFT_PLAY_CONTROLLERS = True) and show the cadence overlay (ENABLE_CADENCE_OVERLAY = True).
	•	Click on the game window to give it focus.
