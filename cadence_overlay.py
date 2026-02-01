import argparse
import json
import socket
import sys
import tkinter as tk


def _safe_decode(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Always-on-top cadence overlay (UDP listener).")
    parser.add_argument("--port", type=int, default=49555)
    parser.add_argument("--x", type=int, default=10)
    parser.add_argument("--y", type=int, default=10)
    parser.add_argument("--font", type=int, default=18)
    parser.add_argument("--alpha", type=float, default=0.85)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.setblocking(False)

    root = tk.Tk()
    root.overrideredirect(True)
    root.wm_attributes("-topmost", True)
    try:
        root.wm_attributes("-alpha", float(args.alpha))
    except Exception:
        pass

    # Keep it visible on macOS Mission Control / Spaces.
    try:
        root.createcommand(
            "::tk::mac::ReopenApplication",
            lambda *a: root.deiconify(),
        )
    except Exception:
        pass

    root.configure(bg="black")

    label = tk.Label(
        root,
        text="CAD --.- rpm",
        fg="#00ff66",
        bg="black",
        font=("Menlo", args.font, "bold"),
        padx=10,
        pady=6,
    )
    label.pack()

    root.geometry(f"+{args.x}+{args.y}")

    state = {
        "cadence": None,
        "source": None,
    }

    def poll():
        try:
            while True:
                payload, _addr = sock.recvfrom(2048)
                msg = _safe_decode(payload)
                if not msg:
                    continue

                cadence = msg.get("cadence")
                source = msg.get("source")

                if cadence is not None:
                    try:
                        state["cadence"] = float(cadence)
                    except Exception:
                        pass
                if source is not None:
                    state["source"] = str(source)
        except BlockingIOError:
            pass
        except Exception:
            pass

        cadence = state["cadence"]
        source = state["source"]

        if cadence is None:
            text = "CAD --.- rpm"
        else:
            if source:
                text = f"CAD {cadence:5.1f} rpm  ({source})"
            else:
                text = f"CAD {cadence:5.1f} rpm"

        label.configure(text=text)
        root.after(80, poll)

    def on_escape(_event=None):
        root.destroy()

    root.bind("<Escape>", on_escape)

    poll()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
