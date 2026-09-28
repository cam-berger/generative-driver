import json, os

_regs = bytearray(open(os.environ["REGISTER_SHIM_REGS"], "rb").read())
_ack = {int(a, 16) for a in os.environ["REGISTER_SHIM_ACK"].split(",")}
_log = []


def _flush():
    p = os.environ.get("REGISTER_SHIM_LOG")
    if p: json.dump(_log, open(p, "w", encoding="utf-8"))


class I2cIOError(IOError): pass
class I2cNackError(I2cIOError): pass
class I2cTimeoutError(I2cIOError): pass


class I2cPort:
    def __init__(self, addr): self.addr = addr
    def _check(self):
        if self.addr not in _ack: raise I2cNackError("NACK from slave")
    def read_from(self, reg, n, **kw):
        self._check(); _log.append(["read", reg, n]); _flush(); return bytes(_regs[reg:reg + n])
    def write_to(self, reg, data, **kw):
        self._check(); data = bytes(data); _log.append(["write", reg, list(data)]); _flush()
        for i, b in enumerate(data): _regs[(reg + i) & 0xFF] = b
    def read(self, n=0, **kw):
        self._check(); _log.append(["raw_read", None, n]); _flush(); return bytes(_regs[:n])
    def write(self, data, **kw):
        self._check(); _log.append(["raw_write", None, list(bytes(data))]); _flush()
    def exchange(self, out=b"", readlen=0, **kw):
        self._check(); out = bytes(out); _log.append(["exchange", list(out), readlen]); _flush()
        return bytes(_regs[out[0]:out[0] + readlen]) if out and readlen else b""


class I2cController:
    def configure(self, url, **kw): _log.append(["configure", url, kw.get("frequency")]); _flush()
    def get_port(self, addr): return I2cPort(addr)
    def poll(self, addr, write=False, **kw): _log.append(["poll", addr, write]); _flush(); return addr in _ack
    def terminate(self): _flush()
    def close(self): _flush()
    @property
    def configured(self): return True
