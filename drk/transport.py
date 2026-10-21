"""Synchronous USB serial transactions; no background writes or retries."""
import math
import re
import time

from .protocol import ProtocolError, parse_measurement, parse_settings, valid_frame


class WriteUncertainError(RuntimeError):
    """A control write was attempted but its complete readback is unconfirmed."""


class Connection:
    def __init__(self, port, *, timeout=1.0):
        if not isinstance(port, str) or not port.strip():
            raise ValueError("port must be a serial device name, e.g. COM9")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        import serial
        baudrate = 115200
        self.timeout = float(timeout)
        self._last_send_ns = 0
        self._uncertain = False
        self._serial = serial.Serial(port=None, baudrate=baudrate,
                                     bytesize=8, parity="N", stopbits=1,
                                     timeout=min(.02, timeout), write_timeout=timeout,
                                     xonxoff=False, rtscts=False, dsrdtr=False)
        self._serial.dtr = False
        self._serial.rts = False
        self._serial.port = port
        try:
            self._serial.open()
            model = self._text(b"ATGETV\r\n").strip()
            if model != "DRK10020":
                raise ProtocolError(f"expected DRK10020, received {model!r}")
            firmware = self._text(b"ATVER\r\n").strip()
            settings = self.settings()
            panel = self._text(b"ATTBS\r\n", line=False)
            if not re.search(r"Mast_Slove=[01](?:,|$)", panel):
                raise ProtocolError(f"unexpected ATTBS response: {panel!r}")
            self._master = bool(re.search(r"Mast_Slove=0(?:,|$)", panel))
            self._settings = settings
            # Firmware clears both output and compensation bits while OFF.
            # Keep the last ON preference; when opened OFF, use vendor default.
            self._auto_compensation = bool(settings.flags & 2) if settings.flags & 1 else True
            self.info = dict(port=port, baudrate=baudrate, model=model,
                             firmware=firmware, panel=panel,
                             initial_settings=settings.to_dict())
        except BaseException:
            self._serial.close()
            raise

    def close(self):
        self._serial.close()

    def _send(self, payload, *, slow=True):
        # Fixed 20 Hz control rate. No public frequency/interval configuration.
        # AT/setup transactions keep the vendor's conservative 100 ms spacing.
        interval_ns = 100_000_000 if slow else 50_000_000
        remaining = interval_ns - (time.perf_counter_ns() - self._last_send_ns)
        if remaining > 0:
            time.sleep(remaining / 1e9)
        self._serial.reset_input_buffer()
        self._last_send_ns = time.perf_counter_ns()
        if self._serial.write(payload) != len(payload):
            raise OSError("partial serial write")
        return self._last_send_ns

    def _text(self, command, *, line=True):
        self._send(command)
        data = bytearray()
        end = time.perf_counter() + self.timeout
        last_rx = time.perf_counter()
        while time.perf_counter() < end:
            chunk = self._serial.read(min(4096, self._serial.in_waiting or 1))
            if chunk:
                data.extend(chunk)
                last_rx = time.perf_counter()
            if len(data) > 2048:
                raise ProtocolError("oversized AT reply")
            if line and b"\r\n" in data:
                return bytes(data).split(b"\r\n", 1)[0].decode("ascii")
            if not line and data and time.perf_counter() - last_rx >= min(.08, self.timeout / 3):
                return data.decode("ascii")
        raise TimeoutError(f"no complete reply to {command!r}")

    def _receive_frame(self, header, tail, length):
        data = bytearray()
        end = time.perf_counter() + self.timeout
        corrupted = False
        while time.perf_counter() < end:
            data.extend(self._serial.read(min(4096, self._serial.in_waiting or 1)))
            while True:
                start = data.find(header)
                if start < 0:
                    data[:] = data[-1:]
                    break
                if start:
                    del data[:start]
                if len(data) < length:
                    break
                frame = bytes(data[:length])
                if valid_frame(frame, header, tail, length):
                    return frame
                corrupted = True
                del data[0]
        if corrupted:
            raise ProtocolError("received corrupt frame; no valid replacement before timeout")
        raise TimeoutError(f"no complete {length}-byte USB reply")

    def settings(self):
        self._send(b"ATRCON\r\n")
        return parse_settings(self._receive_frame(b"\xcc\xdd", b"\x77\x88", 23))

    def _assert_master(self):
        panel = self._text(b"ATTBS\r\n", line=False)
        if not re.search(r"Mast_Slove=0(?:,|$)", panel):
            raise ProtocolError("USB control requires master mode (Mast_Slove=0)")

    def exchange(self, voltage, current):
        if self._uncertain:
            raise WriteUncertainError("previous exchange unconfirmed; disable() or close/open before writing")
        if not self._master:
            raise ProtocolError("USB control requires master mode (Mast_Slove=0)")
        desired = self._settings.update(voltage=voltage, current=current)
        try:
            sent_at = self._send(desired.to_bytes(), slow=False)
            frame = self._receive_frame(b"\xaa\xbb", b"\x55\x66", 288)
            received_at = time.perf_counter_ns()
            result = parse_measurement(frame)
        except BaseException as exc:
            self._uncertain = True
            if not isinstance(exc, Exception):
                raise
            raise WriteUncertainError(
                "control write/measurement unconfirmed; no automatic retry; disable() or close/open"
            ) from exc
        self._settings = desired
        result['sent_at_ns'] = sent_at
        result['received_at_ns'] = received_at
        return result

    def set_enabled(self, enabled):
        # Rare operations refresh physical panel state and verify all setpoints.
        # disable() remains available after an uncertain exchange.
        if enabled and self._uncertain:
            raise WriteUncertainError("close/open to resynchronize before enabling output")
        self._assert_master()
        current = self.settings()
        if current.flags & 1:
            self._auto_compensation = bool(current.flags & 2)
        flags = current.flags & ~3
        if enabled:
            flags |= 3 if self._auto_compensation else 1
        desired = current.update(flags=flags)
        try:
            # Always send an explicit enable/disable request, even for a no-op.
            self._send(desired.to_bytes())
            actual = self.settings()
            if actual != desired:
                raise ProtocolError(f"settings readback mismatch: {actual.to_dict()!r}")
        except BaseException as exc:
            self._uncertain = True
            if not isinstance(exc, Exception):
                raise
            raise WriteUncertainError(
                "output switch write unconfirmed; inspect front panel; no automatic retry"
            ) from exc
        self._master = True
        self._settings = actual
        self._uncertain = False
        return actual.to_dict()
