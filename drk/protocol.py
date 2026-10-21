"""DRK10020 USB panel protocol. V/A float32 values, network byte order."""
from dataclasses import dataclass, replace
import math
import struct


class ProtocolError(RuntimeError):
    """A received frame or value violates the documented USB protocol."""


@dataclass(frozen=True)
class Settings:
    flags: int
    voltage: float
    current: float
    ovp: float
    ocp: float

    def to_bytes(self):
        body = b"\xcc\xdd\x01" + bytes([self.flags])
        body += struct.pack(">4f", self.voltage, self.current, self.ovp, self.ocp)
        body += b"\x77\x88"
        return body + bytes([sum(body) & 255])

    def to_dict(self):
        return dict(voltage=self.voltage, current=self.current,
                    ovp=self.ovp, ocp=self.ocp, enabled=bool(self.flags & 1),
                    ovp_enabled=bool(self.flags & 4), ocp_enabled=bool(self.flags & 8),
                    remote_sense=bool(self.flags & 16), cc_priority=bool(self.flags & 64),
                    flags=self.flags)

    def update(self, **changes):
        limits = {"voltage": (0, 100), "current": (0.001, 20),
                  "ovp": (0.1, 110), "ocp": (0.1, 23)}
        for name, value in changes.items():
            if name == "flags":
                if type(value) is not int or not 0 <= value <= 255:
                    raise ValueError("flags must be a byte")
                continue
            low, high = limits[name]
            if isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            value = float(value)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be in [{low}, {high}]")
            changes[name] = value
        # Round to wire float32 before comparing readback.
        candidate = replace(self, **changes)
        return parse_settings(candidate.to_bytes())


def valid_frame(frame, header, tail, length):
    return (len(frame) == length and frame[:2] == header
            and frame[-3:-1] == tail and sum(frame[:-1]) & 255 == frame[-1])


def parse_settings(frame):
    if not valid_frame(frame, b"\xcc\xdd", b"\x77\x88", 23) or frame[2] != 1:
        raise ProtocolError("invalid 23-byte settings frame")
    values = struct.unpack_from(">4f", frame, 4)
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ProtocolError("non-finite or negative settings value")
    return Settings(frame[3], *values)


def parse_measurement(frame):
    if not valid_frame(frame, b"\xaa\xbb", b"\x55\x66", 288):
        raise ProtocolError("invalid 288-byte measurement frame")
    mcu_v, mcu_i = struct.unpack_from(">2f", frame, 13)
    values = struct.unpack_from(">66f", frame, 21)
    if not all(math.isfinite(v) for v in (mcu_v, mcu_i, *values)):
        raise ProtocolError("non-finite measurement")
    voltages, currents, remotes = values[:22], values[22:44], values[44:]
    flags = frame[3]
    return dict(
        voltage=sum(voltages) / 22, current=sum(currents) / 22,
        power=sum(v * i for v, i in zip(voltages, currents)) / 22,
        remote_voltage=sum(remotes) / 22, temperature=frame[6],
        aux_voltage=int.from_bytes(frame[7:10], "big") / 1000,
        acdc_voltage=int.from_bytes(frame[10:13], "big") / 1000,
        mcu_voltage=mcu_v, mcu_current=mcu_i,
        enabled=bool(flags & 1), running=not bool(flags & 128),
        constant_voltage=bool(flags & 8), ovp_tripped=bool(flags & 16),
        ocp_tripped=bool(flags & 32), fault=int.from_bytes(frame[4:6], "big"),
        status=flags, panel_status=frame[2],
        voltage_samples=voltages, current_samples=currents,
        remote_voltage_samples=remotes,
    )
