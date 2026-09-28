class FtdiError(IOError): pass


class Ftdi:
    @staticmethod
    def show_devices(*a, **k): print("shim: no devices")
