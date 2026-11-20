"""The I2C bus the acquire stage reads through an FTDI adapter.

This is what is left of the runner of the register model, which is deleted
(docs/adr/0010-a-package-is-data-in-one-format.md): `acquire_bus_scan` and `acquire_register_sweep` poll and read a
bus the operator selected by its `ftdi://` URL. Address discovery polls in the write direction, because a
read-direction poll leaves a register device mid-transaction, so the next transfer can fail or return the wrong
bytes. `pyftdi` is loaded when a bus is opened, never at import.
"""

hx = lambda x: int(str(x), 16) if str(x).lower().startswith("0x") else int(x)


class FtdiI2c:
    def __init__(self, url=None, speed_hz=100000):
        if not isinstance(url, str) or not url.startswith("ftdi://"):
            raise ValueError("Select an explicit ftdi:// adapter URL")
        from pyftdi.i2c import I2cController
        self.i2c = I2cController()
        self.i2c.configure(url, frequency=speed_hz)
        self.ports = {}

    def port(self, addr):
        if addr not in self.ports:
            self.ports[addr] = self.i2c.get_port(addr)
        return self.ports[addr]

    def probe(self, addr):
        return self.i2c.poll(addr, write=True)

    def reg_read(self, addr, reg, n):
        return bytes(self.port(addr).read_from(reg, n))

    def close(self):
        self.i2c.terminate()
