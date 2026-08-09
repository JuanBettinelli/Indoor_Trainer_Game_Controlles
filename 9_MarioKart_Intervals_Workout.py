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
from pynput.keyboard import Controller, Key
from pycycling.fitness_machine_service import FitnessMachineService

from csc_cadence_sensor import CSC_MEASUREMENT_UUID, CSCCadenceCalculator, parse_csc_measurement
from device_config import DEFAULT_CONFIG_PATH, configured_address, configured_addresses, load_device_config
from erg_workout_runner import WorkoutTimeline, format_seconds, workout_preview
from intervals_workout import (
    Workout,
    WorkoutError,
    list_ride_events,
    load_workouts_from_plan,
    workout_from_event,
)
from overlay_udp import OverlayClient, OverlayConfig
from runtime_devices import LEGACY_CADENCE_SENSOR_ADDRESS, LEGACY_INDOOR_TRAINER_ADDRESS
from zwift_play_to_keyboard import run_zwift_play_mapper_forever


DEVICE_ADDRESS = LEGACY_INDOOR_TRAINER_ADDRESS
ENABLE_ZWIFT_PLAY_CONTROLLERS = True
USE_EXTERNAL_CADENCE_SENSOR = True
CADENCE_SENSOR_ADDRESS = LEGACY_CADENCE_SENSOR_ADDRESS
CADENCE_SENSOR_STALE_SECONDS = 3.0
CADENCE_SENSOR_RECONNECT_INTERVAL_SECONDS = 5.0
TRAINER_ZERO_CADENCE_POWER_WATTS = 5.0
ENABLE_CADENCE_OVERLAY = False
OVERLAY_AUTOSTART = True
OVERLAY_PORT = 49555

UPPER_CADENCE_THRESHOLD = 65.0
LOWER_CADENCE_THRESHOLD = 30.0
BOOST_CADENCE_THRESHOLD = 100.0

keyboard = Controller()
current_keys_pressed: set[object] = set()
cadence_calculator = CSCCadenceCalculator(stale_seconds=CADENCE_SENSOR_STALE_SECONDS)
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
    cadence_address: str | None,
    zwift_play_addresses: list[str] | None,
) -> None:
    latest = {"power": 0.0, "cadence": 0.0, "speed": 0.0, "source": "Trainer"}
    start_time = time.monotonic()
    last_log = 0.0

    async with BleakClient(address) as trainer_client:
        trainer = FitnessMachineService(trainer_client)
        overlay.start()
        await trainer.enable_control_point_indicate()
        await trainer.request_control()

        zwift_tasks: list[asyncio.Task] = []
        if ENABLE_ZWIFT_PLAY_CONTROLLERS:
            if zwift_play_addresses is None:
                zwift_tasks.append(
                    asyncio.create_task(
                        run_zwift_play_mapper_forever(rescan_interval_seconds=5.0),
                        name="zwift-play-mapper",
                    )
                )
            else:
                for index, controller_address in enumerate(zwift_play_addresses, 1):
                    zwift_tasks.append(
                        asyncio.create_task(
                            run_zwift_play_mapper_forever(
                                target_address=controller_address,
                                max_controllers=1,
                                rescan_interval_seconds=5.0,
                            ),
                            name=f"zwift-play-mapper-{index}",
                        )
                    )
            if zwift_tasks:
                print(f"Zwift Play mapper started for {len(zwift_tasks)} controller task(s).")

        cadence_client = None
        cadence_notify_active = False
        last_reconnect_attempt = 0.0

        def on_csc(_sender: int, payload: bytearray) -> None:
            crank_sample, _, _ = parse_csc_measurement(bytes(payload))
            if crank_sample is not None:
                cadence_calculator.update_from_crank_sample(crank_sample)

        async def ensure_cadence_sensor() -> None:
            nonlocal cadence_client, cadence_notify_active, last_reconnect_attempt
            if not USE_EXTERNAL_CADENCE_SENSOR or not cadence_address:
                return
            now = asyncio.get_event_loop().time()
            if cadence_client is not None and cadence_client.is_connected and cadence_notify_active:
                return
            if now - last_reconnect_attempt < CADENCE_SENSOR_RECONNECT_INTERVAL_SECONDS:
                return
            last_reconnect_attempt = now
            try:
                if cadence_client is None:
                    cadence_client = BleakClient(cadence_address)
                if not cadence_client.is_connected:
                    await cadence_client.connect()
                    cadence_notify_active = False
                if not cadence_notify_active:
                    await cadence_client.start_notify(CSC_MEASUREMENT_UUID, on_csc)
                    cadence_notify_active = True
                    print("External cadence sensor connected.")
            except Exception:
                cadence_notify_active = False
                try:
                    if cadence_client is not None:
                        await cadence_client.disconnect()
                except Exception:
                    pass

        def on_bike_data(data) -> None:
            power = float(data.instant_power or 0)
            trainer_cadence = float(data.instant_cadence or 0)
            if power <= TRAINER_ZERO_CADENCE_POWER_WATTS:
                trainer_cadence = 0
            if USE_EXTERNAL_CADENCE_SENSOR and cadence_address and cadence_calculator.is_fresh:
                cadence = cadence_calculator.cadence_rpm_last
                source = "Garmin"
            else:
                cadence = trainer_cadence
                source = "Trainer"
            latest.update(power=power, cadence=cadence, speed=float(data.instant_speed or 0), source=source)
            overlay.send(cadence, source)
            apply_game_key_mapping(cadence)

        trainer.set_indoor_bike_data_handler(on_bike_data)
        await trainer.enable_indoor_bike_data_notify()

        last_target: int | None | object = object()
        last_step = -1
        erg_active = False
        try:
            while True:
                await ensure_cadence_sensor()
                elapsed = time.monotonic() - start_time
                state = timeline.state_at(elapsed)
                if state.finished:
                    print("\nWorkout complete.")
                    break
                if state.step_index != last_step:
                    print(f"\nStep {state.step_index + 1}/{len(timeline.workout.steps)}: {state.step.name}")
                    last_step = state.step_index

                if state.target_watts is None:
                    if erg_active:
                        await trainer.reset()
                        erg_active = False
                        last_target = None
                        print("ERG disabled for freeride step.")
                elif state.target_watts != last_target:
                    if not erg_active:
                        await trainer.request_control()
                        await trainer.start_or_resume()
                        erg_active = True
                    await trainer.set_target_power(state.target_watts)
                    last_target = state.target_watts

                now = time.monotonic()
                if now - last_log >= 1.0:
                    target = "FREE" if state.target_watts is None else f"{state.target_watts:3d} W"
                    cadence_target = ""
                    if state.step.cadence_min is not None:
                        cadence_target = f" | cadence target {state.step.cadence_min}"
                        if state.step.cadence_max != state.step.cadence_min:
                            cadence_target += f"-{state.step.cadence_max}"
                    print(
                        f"{target} | remaining {format_seconds(state.remaining_in_step)} | "
                        f"power {latest['power']:.0f} W | cadence {latest['cadence']:.1f} rpm "
                        f"({latest['source']}){cadence_target}"
                    )
                    last_log = now
                await asyncio.sleep(0.25)
        finally:
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
            if cadence_client is not None:
                try:
                    if cadence_client.is_connected and cadence_notify_active:
                        await cadence_client.stop_notify(CSC_MEASUREMENT_UUID)
                    await cadence_client.disconnect()
                except Exception:
                    pass
            overlay.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=parse_date, default=dt.date.today(), help="Workout date: YYYY-MM-DD or today")
    parser.add_argument("--event-id", help="Select an Intervals event without an interactive prompt")
    parser.add_argument("--local-plan", type=Path, help="Read workouts from an AI_Coach plan JSON instead of the API")
    parser.add_argument("--ftp", type=positive_int, help="FTP in watts")
    parser.add_argument("--max-watts", type=positive_int, default=600, help="Hard safety ceiling (default: 600 W)")
    parser.add_argument("--devices", type=Path, default=DEFAULT_CONFIG_PATH, help="Device configuration JSON")
    parser.add_argument("--trainer", help="Override the configured FTMS trainer address")
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
        if not args.yes and input("Start workout? [y/N]: ").strip().lower() not in {"y", "yes", "j", "ja"}:
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
            play_addresses = configured_addresses(config, "zwift_play") if config_exists else None
            print(f"Trainer: {trainer_address}")
            print(f"Cadence sensor: {cadence_address or 'trainer cadence'}")
            if play_addresses is not None:
                print(f"Zwift Play controllers: {len(play_addresses)} configured")
            asyncio.run(run_trainer(timeline, trainer_address, cadence_address, play_addresses))
        return 0
    except (WorkoutError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
