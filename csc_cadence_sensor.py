import time
from dataclasses import dataclass
from typing import Optional, Tuple


def _u16_delta(current: int, previous: int) -> int:
    return (current - previous) & 0xFFFF


def _u32_delta(current: int, previous: int) -> int:
    return (current - previous) & 0xFFFFFFFF


@dataclass
class CSCCrankSample:
    cumulative_crank_revolutions: int
    last_crank_event_time_1_1024s: int


class CSCCadenceCalculator:
    """Calculate cadence (RPM) from Cycling Speed & Cadence (CSC) crank data.

    Garmin Cadence Sensor 2 advertises the CSC service and sends CSC Measurement
    notifications with crank revolution + event time.

    Event time units are 1/1024 seconds and wrap at 65536.
    Cumulative crank revolutions is uint16 and also wraps at 65536.
    """

    def __init__(self, stale_seconds: float = 3.0):
        self._prev: Optional[CSCCrankSample] = None
        self._cadence_rpm: float = 0.0
        self._last_update_monotonic: Optional[float] = None
        self._stale_seconds = float(stale_seconds)

    @property
    def is_fresh(self) -> bool:
        if self._last_update_monotonic is None:
            return False
        return (time.monotonic() - self._last_update_monotonic) <= self._stale_seconds

    @property
    def cadence_rpm_last(self) -> float:
        """Last computed cadence, without applying staleness logic."""
        return self._cadence_rpm

    @property
    def cadence_rpm(self) -> float:
        if self._last_update_monotonic is None:
            return 0.0
        if (time.monotonic() - self._last_update_monotonic) > self._stale_seconds:
            return 0.0
        return self._cadence_rpm

    def update_from_crank_sample(self, sample: CSCCrankSample) -> float:
        if self._prev is None:
            self._prev = sample
            self._cadence_rpm = 0.0
            self._last_update_monotonic = time.monotonic()
            return self._cadence_rpm

        delta_revs = _u16_delta(sample.cumulative_crank_revolutions, self._prev.cumulative_crank_revolutions)
        delta_time_ticks = _u16_delta(sample.last_crank_event_time_1_1024s, self._prev.last_crank_event_time_1_1024s)

        self._prev = sample

        # Important: many sensors may still send notifications while stopped.
        # Only mark the sample as "fresh" when the crank event actually advances.
        if delta_revs == 0 or delta_time_ticks == 0:
            return self._cadence_rpm

        self._last_update_monotonic = time.monotonic()

        delta_time_seconds = delta_time_ticks / 1024.0
        self._cadence_rpm = (delta_revs / delta_time_seconds) * 60.0
        return self._cadence_rpm


CSC_MEASUREMENT_UUID = "00002a5b-0000-1000-8000-00805f9b34fb"


def parse_csc_measurement(payload: bytes) -> Tuple[Optional[CSCCrankSample], bool, bool]:
    """Parse CSC Measurement characteristic payload.

    Returns:
        (crank_sample_or_none, wheel_data_present, crank_data_present)

    Notes:
        Flags (byte 0):
            bit 0: Wheel Revolution Data Present
            bit 1: Crank Revolution Data Present
    """

    if not payload:
        return None, False, False

    flags = payload[0]
    wheel_present = bool(flags & 0x01)
    crank_present = bool(flags & 0x02)

    idx = 1

    if wheel_present:
        if len(payload) < idx + 4 + 2:
            return None, wheel_present, crank_present
        cumulative_wheel_revs = int.from_bytes(payload[idx : idx + 4], byteorder="little", signed=False)
        idx += 4
        last_wheel_time = int.from_bytes(payload[idx : idx + 2], byteorder="little", signed=False)
        idx += 2
        _ = (cumulative_wheel_revs, last_wheel_time)  # parsed but not used for cadence

    crank_sample: Optional[CSCCrankSample] = None
    if crank_present:
        if len(payload) < idx + 2 + 2:
            return None, wheel_present, crank_present
        cumulative_crank_revs = int.from_bytes(payload[idx : idx + 2], byteorder="little", signed=False)
        idx += 2
        last_crank_time = int.from_bytes(payload[idx : idx + 2], byteorder="little", signed=False)
        idx += 2
        crank_sample = CSCCrankSample(
            cumulative_crank_revolutions=cumulative_crank_revs,
            last_crank_event_time_1_1024s=last_crank_time,
        )

    return crank_sample, wheel_present, crank_present
