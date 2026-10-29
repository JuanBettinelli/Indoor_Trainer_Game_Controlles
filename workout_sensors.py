"""Collect synchronized data from the configured indoor cycling sensors."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from bleak import BleakClient
from pycycling.cycling_power_service import CyclingPowerService
from pycycling.heart_rate_service import HeartRateService

from csc_cadence_sensor import CSC_MEASUREMENT_UUID, CSCCadenceCalculator, parse_csc_measurement_full


RawCallback = Callable[[str, dict[str, Any]], None]


def _serializable(value: Any) -> Any:
    if hasattr(value, "_asdict"):
        return {key: _serializable(item) for key, item in value._asdict().items()}
    if is_dataclass(value):
        return {key: _serializable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if hasattr(value, "name") and hasattr(value, "value"):
        return value.name
    return value


class RevolutionRateCalculator:
    def __init__(self, event_ticks_per_second: float):
        self.event_ticks_per_second = event_ticks_per_second
        self.previous: tuple[int, int] | None = None
        self.rpm = 0.0

    def update(self, revolutions: int, event_time: int) -> float:
        if self.previous is None:
            self.previous = (revolutions, event_time)
            return 0.0
        previous_revs, previous_time = self.previous
        self.previous = (revolutions, event_time)
        delta_revs = (revolutions - previous_revs) & 0xFFFF
        delta_ticks = (event_time - previous_time) & 0xFFFF
        if delta_revs and delta_ticks:
            self.rpm = delta_revs * 60.0 * self.event_ticks_per_second / delta_ticks
        return self.rpm


class WheelSpeedCalculator:
    def __init__(self, circumference_m: float):
        self.circumference_m = circumference_m
        self.previous: tuple[int, int] | None = None
        self.speed_mps = 0.0
        self.distance_m = 0.0

    def update(self, revolutions: int, event_time: int) -> tuple[float, float]:
        if self.previous is None:
            self.previous = (revolutions, event_time)
            return 0.0, self.distance_m
        previous_revs, previous_time = self.previous
        self.previous = (revolutions, event_time)
        delta_revs = (revolutions - previous_revs) & 0xFFFFFFFF
        delta_ticks = (event_time - previous_time) & 0xFFFF
        self.distance_m += delta_revs * self.circumference_m
        if delta_revs and delta_ticks:
            self.speed_mps = delta_revs * self.circumference_m * 1024.0 / delta_ticks
        return self.speed_mps, self.distance_m


class WorkoutSensorHub:
    def __init__(self, *, wheel_circumference_m: float = 2.105, raw_callback: RawCallback | None = None):
        self.raw_callback = raw_callback
        self.wheel = WheelSpeedCalculator(wheel_circumference_m)
        # Cycling Power Service crank event time uses 1/1024-second units.
        # Using 2048 doubles cadence from pedal power meters such as Assioma.
        self.power_crank = RevolutionRateCalculator(1024.0)
        self.cadence_calculator = CSCCadenceCalculator(stale_seconds=3.0)
        self._last_power_crank: tuple[int, int] | None = None
        self._last_cadence_crank: tuple[int, int] | None = None
        self.latest: dict[str, Any] = {
            "trainer_power": None,
            "trainer_cadence": None,
            "trainer_speed": None,
            "trainer_distance": None,
            "power_meter_power": None,
            "power_meter_cadence": None,
            "power_balance": None,
            "cadence_sensor_cadence": None,
            "speed_sensor_speed": None,
            "speed_sensor_distance": None,
            "heart_rate": None,
            "rr_intervals": [],
        }
        self.updated: dict[str, float] = {}
        self.tasks: list[asyncio.Task] = []
        self.stop_event = asyncio.Event()

    def _raw(self, source: str, payload: Any) -> None:
        if self.raw_callback:
            self.raw_callback(source, _serializable(payload))

    def update_trainer(self, data: Any) -> None:
        now = time.monotonic()
        self.latest.update(
            trainer_power=float(data.instant_power or 0),
            trainer_cadence=float(data.instant_cadence or 0),
            trainer_speed=float(data.instant_speed or 0) / 3.6,
            trainer_distance=float(data.total_distance) if data.total_distance is not None else None,
        )
        self.updated["trainer"] = now
        self._raw("indoor_trainer", data)

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        power_fresh = now - self.updated.get("power_meter", -1e9) <= 3.0
        power_cadence_fresh = now - self.updated.get("power_meter_cadence", -1e9) <= 3.0
        cadence_fresh = now - self.updated.get("cadence_sensor", -1e9) <= 3.0
        speed_fresh = now - self.updated.get("speed_sensor", -1e9) <= 3.0
        hr_fresh = now - self.updated.get("heart_rate", -1e9) <= 5.0
        power = self.latest["power_meter_power"] if power_fresh else self.latest["trainer_power"]
        if power_cadence_fresh and self.latest["power_meter_cadence"] is not None:
            cadence = self.latest["power_meter_cadence"]
            cadence_source = "power_meter"
        elif cadence_fresh:
            cadence = self.latest["cadence_sensor_cadence"]
            cadence_source = "cadence_sensor"
        else:
            cadence = self.latest["trainer_cadence"]
            cadence_source = "trainer"
        speed = self.latest["speed_sensor_speed"] if speed_fresh else self.latest["trainer_speed"]
        distance = self.latest["speed_sensor_distance"] if speed_fresh else self.latest["trainer_distance"]
        return {
            **self.latest,
            "power": power,
            "cadence": cadence,
            "cadence_source": cadence_source,
            "speed": speed,
            "distance": distance,
            "heart_rate": self.latest["heart_rate"] if hr_fresh else None,
        }

    def start(self, *, power_meter: str | None, cadence_sensor: str | None, speed_sensor: str | None, heart_rate_sensor: str | None) -> None:
        specifications = (
            ("power meter", power_meter, self._run_power_meter),
            ("cadence sensor", cadence_sensor, self._run_cadence_sensor),
            ("speed sensor", speed_sensor, self._run_speed_sensor),
            ("heart-rate sensor", heart_rate_sensor, self._run_heart_rate_sensor),
        )
        for label, address, runner in specifications:
            if address:
                self.tasks.append(asyncio.create_task(self._reconnecting(label, address, runner), name=f"sensor-{label}"))

    async def stop(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _reconnecting(self, label: str, address: str, runner: Callable[[BleakClient], Any]) -> None:
        while not self.stop_event.is_set():
            try:
                async with BleakClient(address) as client:
                    await runner(client)
                    print(f"{label.capitalize()} connected.")
                    await self.stop_event.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"{label.capitalize()} unavailable: {exc}. Retrying in 5 s.")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

    async def _run_power_meter(self, client: BleakClient) -> None:
        service = CyclingPowerService(client)

        def measurement(data: Any) -> None:
            self.latest["power_meter_power"] = float(data.instantaneous_power)
            self.latest["power_balance"] = float(data.pedal_power_balance) / 2.0 if data.pedal_power_balance is not None else None
            if data.cumulative_crank_revs is not None and data.last_crank_event_time is not None:
                current_crank = (data.cumulative_crank_revs, data.last_crank_event_time)
                if current_crank != self._last_power_crank:
                    self.latest["power_meter_cadence"] = self.power_crank.update(*current_crank)
                    self.updated["power_meter_cadence"] = time.monotonic()
                    self._last_power_crank = current_crank
            self.updated["power_meter"] = time.monotonic()
            self._raw("power_meter_measurement", data)

        service.set_cycling_power_measurement_handler(measurement)
        await service.enable_cycling_power_measurement_notifications()
        try:
            service.set_cycling_power_vector_handler(lambda data: self._raw("power_meter_vector", data))
            await service.enable_cycling_power_vector_notifications()
        except Exception:
            pass
        try:
            feature = await service.get_cycling_power_feature()
            self._raw("power_meter_feature", feature)
        except Exception:
            pass
        try:
            location = await service.get_sensor_location()
            self._raw("power_meter_location", {"location": _serializable(location)})
        except Exception:
            pass

    async def _run_cadence_sensor(self, client: BleakClient) -> None:
        def notification(_sender: Any, payload: bytearray) -> None:
            crank, _wheel = parse_csc_measurement_full(bytes(payload))
            if crank is not None:
                current_crank = (crank.cumulative_crank_revolutions, crank.last_crank_event_time_1_1024s)
                if current_crank != self._last_cadence_crank:
                    self.latest["cadence_sensor_cadence"] = self.cadence_calculator.update_from_crank_sample(crank)
                    self.updated["cadence_sensor"] = time.monotonic()
                    self._last_cadence_crank = current_crank
            self._raw("cadence_sensor", {"payload_hex": bytes(payload).hex(), "crank": crank})

        await client.start_notify(CSC_MEASUREMENT_UUID, notification)

    async def _run_speed_sensor(self, client: BleakClient) -> None:
        def notification(_sender: Any, payload: bytearray) -> None:
            _crank, wheel = parse_csc_measurement_full(bytes(payload))
            if wheel is not None:
                previous_wheel = self.wheel.previous
                speed, distance = self.wheel.update(
                    wheel.cumulative_wheel_revolutions, wheel.last_wheel_event_time_1_1024s
                )
                self.latest["speed_sensor_speed"] = speed
                self.latest["speed_sensor_distance"] = distance
                current_wheel = (wheel.cumulative_wheel_revolutions, wheel.last_wheel_event_time_1_1024s)
                if current_wheel != previous_wheel:
                    self.updated["speed_sensor"] = time.monotonic()
            self._raw("speed_sensor", {"payload_hex": bytes(payload).hex(), "wheel": wheel})

        await client.start_notify(CSC_MEASUREMENT_UUID, notification)

    async def _run_heart_rate_sensor(self, client: BleakClient) -> None:
        service = HeartRateService(client)

        def measurement(data: Any) -> None:
            self.latest["heart_rate"] = int(data.bpm)
            self.latest["rr_intervals"] = [value / 1024.0 for value in data.rr_interval]
            self.updated["heart_rate"] = time.monotonic()
            self._raw("heart_rate", data)

        service.set_hr_measurement_handler(measurement)
        await service.enable_hr_measurement_notifications()
