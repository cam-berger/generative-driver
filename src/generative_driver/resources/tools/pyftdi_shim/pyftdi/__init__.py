"""A stand-in for pyftdi that serves a register file instead of a bus (memorial M7: package fidelity).
Activated by putting tools/pyftdi_shim first on PYTHONPATH. BME_SHIM_REGS names a 256-byte register image;
BME_SHIM_LOG names a JSON file that receives every access the driver makes."""
