"""Distinguish deliberate keyboard stop commands from controller-generated keys."""

from __future__ import annotations

from typing import Any

from pynput.keyboard import Key


def is_finish_key(key: Any, injected: bool = False) -> bool:
    """Return true only for a physical Q or F12 press.

    Zwift Play mappings emit synthetic keyboard events through pynput. On
    macOS those events are marked as injected and must never stop a workout.
    """
    if injected:
        return False
    character = getattr(key, "char", None)
    return key == Key.f12 or (isinstance(character, str) and character.lower() == "q")
