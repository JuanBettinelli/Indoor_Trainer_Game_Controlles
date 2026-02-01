import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


DEFAULT_OVERLAY_PORT = 49555


@dataclass
class OverlayConfig:
    enabled: bool = True
    autostart: bool = True
    port: int = DEFAULT_OVERLAY_PORT
    x: int = 10
    y: int = 10
    font: int = 18
    alpha: float = 0.85


class OverlayClient:
    def __init__(self, config: OverlayConfig):
        self.config = config
        self._sock: Optional[socket.socket] = None
        self._proc: Optional[subprocess.Popen] = None
        self._last_send_time = 0.0
        self._min_interval_seconds = 0.10  # 10 Hz

    def start(self) -> None:
        if not self.config.enabled:
            return

        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        if self.config.autostart and self._proc is None:
            overlay_path = os.path.join(os.path.dirname(__file__), "cadence_overlay.py")
            if os.path.exists(overlay_path):
                try:
                    self._proc = subprocess.Popen(
                        [
                            sys.executable,
                            overlay_path,
                            "--port",
                            str(self.config.port),
                            "--x",
                            str(self.config.x),
                            "--y",
                            str(self.config.y),
                            "--font",
                            str(self.config.font),
                            "--alpha",
                            str(self.config.alpha),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    self._proc = None

    def send(self, cadence_rpm: float, source: str = "") -> None:
        if not self.config.enabled:
            return
        if self._sock is None:
            return

        now = time.monotonic()
        if (now - self._last_send_time) < self._min_interval_seconds:
            return
        self._last_send_time = now

        try:
            payload = json.dumps({"cadence": float(cadence_rpm), "source": source}).encode("utf-8")
            self._sock.sendto(payload, ("127.0.0.1", int(self.config.port)))
        except Exception:
            pass

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                pass
            self._proc = None
