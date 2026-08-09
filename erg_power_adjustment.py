"""Shared manual ERG target adjustment controlled by Zwift Click."""

from __future__ import annotations


class ErgPowerAdjustment:
    def __init__(self, step_watts: int = 5, minimum_watts: int = 0, maximum_watts: int = 1000):
        self.step_watts = step_watts
        self.minimum_watts = minimum_watts
        self.maximum_watts = maximum_watts
        self.offset_watts = 0

    def target(self, base_watts: int) -> int:
        return max(self.minimum_watts, min(self.maximum_watts, int(base_watts) + self.offset_watts))

    def change(self, direction: int, base_watts: int | None = None, *, announce: bool = True) -> int | None:
        previous_offset = self.offset_watts
        self.offset_watts += self.step_watts if direction > 0 else -self.step_watts
        if base_watts is not None:
            self.offset_watts = max(
                self.minimum_watts - int(base_watts),
                min(self.maximum_watts - int(base_watts), self.offset_watts),
            )
            if self.offset_watts == previous_offset:
                if announce:
                    print(f"ERG target unchanged: safety limit reached ({self.target(base_watts)} W)")
                return None
        if base_watts is None:
            if announce:
                print(f"ERG adjustment: {self.offset_watts:+d} W")
            return None
        target = self.target(base_watts)
        if announce:
            print(f"ERG target adjusted to {target} W ({self.offset_watts:+d} W)")
        return target
