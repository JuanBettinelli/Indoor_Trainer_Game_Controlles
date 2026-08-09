#!/usr/bin/env python3
"""Ride an Intervals.icu workout on an FTMS trainer in ERG mode."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

from bleak import BleakClient
from pynput.keyboard import Controller, Key, Listener
from pycycling.fitness_machine_service import FitnessMachineService

from activity_recording import WorkoutRecorder, require_fit_export
from device_config import DEFAULT_CONFIG_PATH, configured_address, configured_addresses, load_device_config
from erg_power_adjustment import ErgPowerAdjustment
from erg_workout_runner import WorkoutTimeline, format_seconds, interval_countdown_cues, workout_preview
from intervals_workout import (
    Workout,
    WorkoutError,
    is_affirmative_response,
    list_ride_events,
    load_workouts_from_plan,
    upload_activity_file,
    workout_from_event,
)
from overlay_udp import OverlayClient, OverlayConfig
from runtime_devices import LEGACY_CADENCE_SENSOR_ADDRESS, LEGACY_INDOOR_TRAINER_ADDRESS
from workout_sensors import WorkoutSensorHub
from zwift_play_to_keyboard import ZwiftHapticHub, run_zwift_play_mapper_forever


DEVICE_ADDRESS = LEGACY_INDOOR_TRAINER_ADDRESS
ENABLE_ZWIFT_PLAY_CONTROLLERS = True
CADENCE_SENSOR_ADDRESS = LEGACY_CADENCE_SENSOR_ADDRESS
ENABLE_CADENCE_OVERLAY = False
OVERLAY_AUTOSTART = True
OVERLAY_PORT = 49555

UPPER_CADENCE_THRESHOLD = 65.0
LOWER_CADENCE_THRESHOLD = 30.0
BOOST_CADENCE_THRESHOLD = 100.0

keyboard = Controller()
current_keys_pressed: set[object] = set()
overlay = OverlayClient(
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


def apply_game_key_mapping(cadence: float) -> None:
    keys_needed: set[object] = set()
    if cadence > BOOST_CADENCE_THRESHOLD:
        keys_needed.update(("a", Key.up))
    elif cadence > UPPER_CADENCE_THRESHOLD:
        keys_needed.add("a")
    elif cadence < LOWER_CADENCE_THRESHOLD:
        keys_needed.add("b")

    global current_keys_pressed
    for key in current_keys_pressed - keys_needed:
        keyboard.release(key)
    for key in keys_needed - current_keys_pressed:
        keyboard.press(key)
    current_keys_pressed = keys_needed


def parse_date(value: str) -> dt.date:
    if value.lower() == "today":
        return dt.date.today()
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD or 'today'") from exc


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be positive")
    return parsed


def choose_workout(workouts: list[Workout], event_id: str | None) -> Workout:
    if not workouts:
        raise WorkoutError("No compatible cycling workout found for the selected date")
    if event_id:
        matches = [workout for workout in workouts if workout.event_id == event_id]
        if len(matches) != 1:
            raise WorkoutError(f"No unique cycling workout found with ID {event_id!r}")
        return matches[0]
    if len(workouts) == 1:
        return workouts[0]
    print("Cycling workouts found:\n")
    for index, workout in enumerate(workouts, 1):
        print(f"{index}. {workout.start_date_local:<20} {workout.name} [{workout.event_id}]")
    try:
        selection = int(input("\nSelect workout: ").strip())
        return workouts[selection - 1]
    except (ValueError, IndexError) as exc:
        raise WorkoutError("Invalid workout selection") from exc


def get_ftp(value: int | None) -> int:
    if value is not None:
        return value
    try:
        return int(input("FTP in watts: ").strip())
    except ValueError as exc:
        raise WorkoutError("FTP must be a whole number") from exc


def load_selected_workout(args: argparse.Namespace) -> Workout:
    if args.local_plan:
        workouts = load_workouts_from_plan(args.local_plan, args.date)
    else:
        events = list_ride_events(args.date)
        workouts = []
        errors = []
        for event in events:
            try:
                workouts.append(workout_from_event(event))
            except WorkoutError as exc:
                errors.append(f"{event.get('name') or event.get('id')}: {exc}")
        if errors:
            print("Skipped incompatible workouts:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
    return choose_workout(workouts, args.event_id)


async def simulate(timeline: WorkoutTimeline, speed: float) -> None:
    print("\nSimulation started. No Bluetooth devices will be contacted.\n")
    simulated = 0.0
    last_step = -1
    while True:
        state = timeline.state_at(simulated)
        if state.finished:
            print("Simulation complete.")
            return
        if state.step_index != last_step:
            target = "ERG off" if state.target_watts is None else f"{state.target_watts} W"
            print(f"Step {state.step_index + 1}/{len(timeline.workout.steps)}: {state.step.name} | {target}")
            last_step = state.step_index
        delay = min(1.0, 1.0 / speed)
        await asyncio.sleep(delay)
        simulated += delay * speed


async def run_trainer(
    timeline: WorkoutTimeline,
    address: str,
    sensor_addresses: dict[str, str | None],
    controller_addresses: list[tuple[str, str]] | None,
    recorder: WorkoutRecorder,
    wheel_circumference_m: float,
) -> tuple[Path, bool]:
    last_log = 0.0
    fit_path: Path | None = None
    power_adjustment = ErgPowerAdjustment(maximum_watts=timeline.max_watts)
    current_base_target: dict[str, int | None] = {"watts": None}
    finish_requested = False

    def on_finish_key(key) -> bool | None:
        nonlocal finish_requested
        character = getattr(key, "char", None)
        is_quit_key = key == Key.f12 or (isinstance(character, str) and character.lower() == "q")
        if is_quit_key and not finish_requested:
            finish_requested = True
            print("\nFinish key pressed: finishing and saving the workout ...")
        return None

    finish_listener = Listener(on_press=on_finish_key)

    def change_erg_target(direction: int) -> bool:
        base_target = current_base_target["watts"]
        if base_target is None:
            print("Zwift Click ignored: ERG is currently disabled.")
            return False
        return power_adjustment.change(direction, base_target) is not None

    async with BleakClient(address) as trainer_client:
        trainer = FitnessMachineService(trainer_client)
        sensor_hub = WorkoutSensorHub(
            wheel_circumference_m=wheel_circumference_m,
            raw_callback=recorder.raw_event,
        )
        haptic_hub = ZwiftHapticHub()
        haptic_tasks: set[asyncio.Task] = set()

        def haptic_finished(task: asyncio.Task) -> None:
            haptic_tasks.discard(task)
            if not task.cancelled():
                task.exception()

        def schedule_haptic(coroutine) -> None:
            task = asyncio.create_task(coroutine)
            haptic_tasks.add(task)
            task.add_done_callback(haptic_finished)
        overlay.start()
        await trainer.enable_control_point_indicate()
        await trainer.request_control()

        zwift_tasks: list[asyncio.Task] = []
        if ENABLE_ZWIFT_PLAY_CONTROLLERS:
            if controller_addresses is None:
                zwift_tasks.append(
                    asyncio.create_task(
                        run_zwift_play_mapper_forever(
                            rescan_interval_seconds=5.0,
                            on_click_power_delta=change_erg_target,
                            haptic_hub=haptic_hub,
                        ),
                        name="zwift-play-mapper",
                    )
                )
            else:
                for index, (controller_type, controller_address) in enumerate(controller_addresses, 1):
                    zwift_tasks.append(
                        asyncio.create_task(
                            run_zwift_play_mapper_forever(
                                target_address=controller_address,
                                max_controllers=1,
                                controller_type=controller_type,
                                rescan_interval_seconds=5.0,
                                on_click_power_delta=change_erg_target,
                                haptic_hub=haptic_hub,
                            ),
                            name=f"zwift-{controller_type}-mapper-{index}",
                        )
                    )
            if zwift_tasks:
                print(f"Zwift controller mapper started for {len(zwift_tasks)} controller task(s).")

        def on_bike_data(data) -> None:
            sensor_hub.update_trainer(data)

        trainer.set_indoor_bike_data_handler(on_bike_data)
        await trainer.enable_indoor_bike_data_notify()
        sensor_hub.start(
            power_meter=sensor_addresses.get("power_meter"),
            cadence_sensor=sensor_addresses.get("cadence_sensor"),
            speed_sensor=sensor_addresses.get("speed_sensor"),
            heart_rate_sensor=sensor_addresses.get("heart_rate_sensor"),
        )

        last_target: int | None | object = object()
        last_step = -1
        previous_remaining: float | None = None
        erg_active = False
        start_time = time.monotonic()
        next_sample = 0.0
        finish_listener.start()
        print("Press Q at any time to finish early and save the workout (F12 also works).")
        try:
            while True:
                elapsed = time.monotonic() - start_time
                state = timeline.state_at(elapsed)
                current_base_target["watts"] = state.target_watts
                if state.finished:
                    print("\nWorkout complete.")
                    break
                if finish_requested:
                    print("Workout ended early by the user.")
                    break
                if state.step_index != last_step:
                    print(f"\nStep {state.step_index + 1}/{len(timeline.workout.steps)}: {state.step.name}")
                    if last_step >= 0:
                        schedule_haptic(haptic_hub.sustained(duration_seconds=1.0))
                    last_step = state.step_index
                    previous_remaining = float(state.step.duration_seconds) + 1e-9

                if previous_remaining is not None and state.step_index + 1 < len(timeline.workout.steps):
                    for pulse_count in interval_countdown_cues(previous_remaining, state.remaining_in_step):
                        schedule_haptic(haptic_hub.pulse(count=pulse_count))
                previous_remaining = state.remaining_in_step

                if state.target_watts is None:
                    if erg_active:
                        await trainer.reset()
                        erg_active = False
                        last_target = None
                        print("ERG disabled for freeride step.")
                else:
                    adjusted_target = power_adjustment.target(state.target_watts)
                if state.target_watts is not None and adjusted_target != last_target:
                    if not erg_active:
                        await trainer.request_control()
                        await trainer.start_or_resume()
                        erg_active = True
                    await trainer.set_target_power(adjusted_target)
                    last_target = adjusted_target

                now = time.monotonic()
                snapshot = sensor_hub.snapshot()
                cadence = float(snapshot.get("cadence") or 0)
                cadence_source = (
                    "Power meter" if snapshot.get("power_meter_cadence") is not None
                    else "Cadence sensor" if snapshot.get("cadence_sensor_cadence") is not None
                    else "Trainer"
                )
                overlay.send(cadence, cadence_source)
                apply_game_key_mapping(cadence)
                if elapsed >= next_sample:
                    recorder.sample(
                        elapsed,
                        state.step_index,
                        state.step.name,
                        power_adjustment.target(state.target_watts) if state.target_watts is not None else None,
                        snapshot,
                    )
                    next_sample += 1.0
                if now - last_log >= 1.0:
                    target = "FREE" if state.target_watts is None else f"{power_adjustment.target(state.target_watts):3d} W"
                    cadence_target = ""
                    if state.step.cadence_min is not None:
                        cadence_target = f" | cadence target {state.step.cadence_min}"
                        if state.step.cadence_max != state.step.cadence_min:
                            cadence_target += f"-{state.step.cadence_max}"
                    print(
                        f"{target} | remaining {format_seconds(state.remaining_in_step)} | "
                        f"power {float(snapshot.get('power') or 0):.0f} W | cadence {cadence:.1f} rpm "
                        f"({cadence_source}) | HR {snapshot.get('heart_rate') or '-'}{cadence_target}"
                    )
                    last_log = now
                await asyncio.sleep(0.25)
        finally:
            finish_listener.stop()
            finish_listener.join(timeout=1.0)
            try:
                await trainer.reset()
            except Exception:
                pass
            for key in current_keys_pressed:
                keyboard.release(key)
            for zwift_task in zwift_tasks:
                zwift_task.cancel()
            for zwift_task in zwift_tasks:
                try:
                    await zwift_task
                except (asyncio.CancelledError, Exception):
                    pass
            for haptic_task in list(haptic_tasks):
                haptic_task.cancel()
            if haptic_tasks:
                await asyncio.gather(*haptic_tasks, return_exceptions=True)
            await sensor_hub.stop()
            overlay.close()
            if recorder.samples:
                fit_path = recorder.finish()
    if fit_path is None:
        raise WorkoutError(f"No samples recorded. Raw log: {recorder.raw_path}")
    return fit_path, finish_requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=parse_date, default=dt.date.today(), help="Workout date: YYYY-MM-DD or today")
    parser.add_argument("--event-id", help="Select an Intervals event without an interactive prompt")
    parser.add_argument("--local-plan", type=Path, help="Read workouts from an AI_Coach plan JSON instead of the API")
    parser.add_argument("--ftp", type=positive_int, help="FTP in watts")
    parser.add_argument("--max-watts", type=positive_int, default=600, help="Hard safety ceiling (default: 600 W)")
    parser.add_argument("--devices", type=Path, default=DEFAULT_CONFIG_PATH, help="Device configuration JSON")
    parser.add_argument("--trainer", help="Override the configured FTMS trainer address")
    parser.add_argument("--recordings-dir", type=Path, default=Path(__file__).with_name("recordings"))
    parser.add_argument("--wheel-circumference-mm", type=positive_int, default=2105)
    parser.add_argument("--no-upload", action="store_true", help="Save locally without uploading to Intervals.icu")
    parser.add_argument("--simulate", action="store_true", help="Run without Bluetooth hardware")
    parser.add_argument("--simulation-speed", type=float, default=60.0, help="Simulation time multiplier")
    parser.add_argument("--yes", action="store_true", help="Start after preview without asking")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        workout = load_selected_workout(args)
        timeline = WorkoutTimeline(workout, get_ftp(args.ftp), args.max_watts)
        print("\n" + workout_preview(timeline) + "\n")
        if not args.simulate:
            require_fit_export()
        if not args.yes and not is_affirmative_response(input("Start workout? [y/N]: ")):
            print("Cancelled before trainer connection.")
            return 0
        if args.simulation_speed <= 0:
            raise WorkoutError("Simulation speed must be positive")
        if args.simulate:
            asyncio.run(simulate(timeline, args.simulation_speed))
        else:
            config_exists = args.devices.exists()
            config = load_device_config(args.devices)
            trainer_address = args.trainer or configured_address(config, "indoor_trainer") or DEVICE_ADDRESS
            cadence_address = configured_address(config, "cadence_sensor")
            if cadence_address is None and not config_exists:
                cadence_address = CADENCE_SENSOR_ADDRESS
            sensor_addresses = {
                "power_meter": configured_address(config, "power_meter"),
                "cadence_sensor": cadence_address,
                "speed_sensor": configured_address(config, "speed_sensor"),
                "heart_rate_sensor": configured_address(config, "heart_rate_sensor"),
            }
            controller_addresses = None
            if config_exists:
                controller_addresses = [("play", address) for address in configured_addresses(config, "zwift_play")]
                controller_addresses.extend(
                    ("click", address) for address in configured_addresses(config, "zwift_click")
                )
            print(f"Trainer: {trainer_address}")
            for sensor_role, sensor_address in sensor_addresses.items():
                print(f"{sensor_role.replace('_', ' ').title()}: {sensor_address or 'not configured'}")
            if controller_addresses is not None:
                print(f"Zwift controllers: {len(controller_addresses)} configured")
            recorder = WorkoutRecorder(workout.name, workout.event_id, args.recordings_dir)
            fit_path, ended_early = asyncio.run(
                run_trainer(
                    timeline,
                    trainer_address,
                    sensor_addresses,
                    controller_addresses,
                    recorder,
                    args.wheel_circumference_mm / 1000.0,
                )
            )
            print(f"\nFIT activity saved: {fit_path}")
            print(f"Raw sensor data saved: {recorder.raw_path}")
            should_upload = not args.no_upload and (
                args.yes or is_affirmative_response(input("Upload completed activity to Intervals.icu? [y/N]: "))
            )
            if should_upload:
                result = upload_activity_file(
                    fit_path,
                    name=workout.name,
                    description=(
                        f"Recorded by Indoor Trainer Game Controls. "
                        f"{'Workout ended early. ' if ended_early else ''}"
                        f"Raw archive: {recorder.raw_path.name}"
                    ),
                    external_id=fit_path.stem,
                )
                activity_id = result.get("id") if isinstance(result, dict) else None
                print(f"Uploaded to Intervals.icu{f' as {activity_id}' if activity_id else ''}.")
        return 0
    except (WorkoutError, RuntimeError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
