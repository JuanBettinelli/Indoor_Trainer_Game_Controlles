#!/usr/bin/env python3
"""Configure keyboard mappings for saved Zwift Play and Zwift Click controllers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from controller_config import (
    DEFAULT_CONFIG_PATH,
    PROFILE_BUTTONS,
    PROFILE_LABELS,
    ControllerConfigError,
    default_config,
    load_controller_config,
)
from device_config import configured_devices, load_device_config


def show_connected_controllers() -> None:
    devices = load_device_config()
    print("Saved controllers:")
    for role in ("zwift_play", "zwift_click"):
        records = configured_devices(devices, role)
        label = "Zwift Play" if role == "zwift_play" else "Zwift Click"
        if records:
            for record in records:
                print(f"  {label}: {record.get('name', 'Unknown')} ({record.get('address', '-')})")
        else:
            print(f"  {label}: not configured")
    print()


def show_mappings(config: dict, path: Path) -> None:
    print(f"Controller mapping: {path}\n")
    print(f"Vibration feedback: {'enabled' if config['settings']['vibration_enabled'] else 'disabled'}\n")
    for profile, label in PROFILE_LABELS.items():
        print(label)
        mapping = config["mappings"][profile]
        for button in PROFILE_BUTTONS[profile]:
            print(f"  {button:<16} -> {mapping.get(button, 'disabled')}")
        print()


def choose_profiles() -> list[str]:
    profiles = list(PROFILE_BUTTONS)
    print("Which controller mapping do you want to edit?")
    for index, profile in enumerate(profiles, 1):
        print(f"{index}. {PROFILE_LABELS[profile]}")
    print(f"{len(profiles) + 1}. All controllers")
    answer = input("Selection [4]: ").strip() or str(len(profiles) + 1)
    try:
        selected = int(answer)
    except ValueError as exc:
        raise ControllerConfigError("Invalid selection") from exc
    if selected == len(profiles) + 1:
        return profiles
    if 1 <= selected <= len(profiles):
        return [profiles[selected - 1]]
    raise ControllerConfigError("Invalid selection")


def edit_profile(config: dict, profile: str) -> None:
    mapping = config["mappings"][profile]
    print(f"\n{PROFILE_LABELS[profile]}")
    print("Enter a keyboard key, press Enter to keep the current value, or '-' to disable.")
    for button in PROFILE_BUTTONS[profile]:
        current = mapping.get(button, "disabled")
        answer = input(f"  {button} [{current}]: ").strip()
        if not answer:
            continue
        if answer == "-":
            mapping.pop(button, None)
        else:
            mapping[button] = answer.lower()


def edit_vibration(config: dict) -> None:
    current = config["settings"]["vibration_enabled"]
    default = "Y/n" if current else "y/N"
    answer = input(f"\nEnable Zwift Play vibration feedback? [{default}]: ").strip().lower()
    if not answer:
        return
    if answer in {"y", "yes", "j", "ja"}:
        config["settings"]["vibration_enabled"] = True
    elif answer in {"n", "no", "nein"}:
        config["settings"]["vibration_enabled"] = False
    else:
        raise ControllerConfigError("Invalid vibration selection")


def atomic_write(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--show", action="store_true", help="Show mappings without editing")
    parser.add_argument("--reset", action="store_true", help="Restore the default mappings")
    parser.add_argument("--yes", action="store_true", help="Save without final confirmation")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config = default_config() if args.reset else load_controller_config(args.output)
        show_connected_controllers()
        if args.show:
            show_mappings(config, args.output)
            return 0
        if not args.reset:
            for profile in choose_profiles():
                edit_profile(config, profile)
            edit_vibration(config)
        print()
        show_mappings(config, args.output)
        if not args.yes and input("Save these mappings? [y/N]: ").strip().lower() not in {"y", "yes", "j", "ja"}:
            print("Cancelled. No file was changed.")
            return 0
        atomic_write(args.output, config)
        print(f"Saved controller mapping to {args.output}")
        return 0
    except ControllerConfigError as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
