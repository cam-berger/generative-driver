"""Device package emitted from an interface-model/1 model (stage 5 of the pipeline, run emit_D1).

The driver is a packaging of the bundled model: every operation it puts on the bus is one the bundled
model.json lists, with the model's registers, bytes and order, and every conversion is the bundled
convert.py. See ../REFERENCE.md for what the device is, how it is wired, the protocol and the safety limits,
and ../EVIDENCE.md for where each of those claims comes from.

API:   from driver import Device;  with Device() as dev: print(dev.measure().values)
CLI:   python -m driver info | read <reg> [n] | write <reg> <byte> [--force] | measure [--n N] | op <name>
"""
from .device import (
    Device, Sample, Safety, DriverError, RefusedError, IdentityError, NackError, BusError,
    PACKAGE_NAME, PACKAGE_VERSION, DEFAULT_URL, MODEL_PATH, load_model, hx, reg_hex,
)

__version__ = PACKAGE_VERSION
__all__ = [
    "Device", "Sample", "Safety", "DriverError", "RefusedError", "IdentityError", "NackError", "BusError",
    "PACKAGE_NAME", "PACKAGE_VERSION", "DEFAULT_URL", "MODEL_PATH", "load_model", "hx", "reg_hex",
]
