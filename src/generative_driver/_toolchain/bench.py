"""Bench adapter: everything that is true of this bench rather than of the pipeline.

One I2C bus is shared by the FT232H and by whatever controller is wired to it. Only one master may drive
it at a time, so a controller running its own firmware has to be parked in its bootloader before the
toolchain can probe, and released afterwards. Parking is ESP-specific; a different bench holds the other
master in reset, or has no other master at all.
"""
import os

from . import core


def _esptool(port, *args, after="hard-reset"):
    esptool = [core.VENV_PY, "-m", "esptool"]
    return core.sh([*esptool, "-p", port, "--after", after, *args], timeout=120)


@core.tool("bus_prepare")
def bus_prepare(run_dir, mode="park", ports=None, backend=None, **_):
    """Make the bus safe for the toolchain to drive, or hand it back.

    An explicit ESP32 backend and nonempty list of selected ports are required. mode 'park' stops only
    those controllers in their bootloaders; mode 'release' resets them back into their firmware.
    USB CDC endpoints belonging to other device classes must never be parked by this adapter.
    """
    reason = None
    if backend != "esp32":
        reason = "bus_prepare requires explicit backend='esp32' for selected ESP32 controllers"
    elif mode not in ("park", "release"):
        reason = "mode must be 'park' or 'release'"
    elif not isinstance(ports, list) or not ports or any(not isinstance(p, str) or not p.strip() for p in ports):
        reason = "Provide an explicit nonempty list of ESP32 ports; automatic device enumeration is disabled"
    if reason:
        return {"mode": mode, "controllers": {}, "bus_master_is_toolchain": False,
                "reason": reason, "_exit": 1}
    results, notes = {}, []
    for p in ports:
        rc, out, err = _esptool(p, "chip-id", after=("no-reset" if mode == "park" else "hard-reset"))
        mac = next((l.split()[-1] for l in out.splitlines() if l.startswith("MAC:")), None)
        results[p] = {"exit": rc, "mac": mac}
        if rc != 0:
            notes.append(f"{p}: esptool exit {rc}; {(err or out).strip().splitlines()[-1][:120] if (err or out).strip() else 'no output'}")
    ok = all(v["exit"] == 0 for v in results.values()) and bool(results)
    return {"mode": mode, "controllers": results, "bus_master_is_toolchain": mode == "park" and ok,
            "_exit": 0 if ok else 1, "_notes": notes}
