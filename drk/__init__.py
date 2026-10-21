"""Single-connection DRK10020 API; V, A, W, degrees Celsius."""
import threading as _threading

from .protocol import ProtocolError
from .transport import Connection as _Connection, WriteUncertainError

__all__ = ['detect', 'open', 'exchange', 'enable', 'disable', 'close',
           'ProtocolError', 'WriteUncertainError']
_device = None
_owner = None
_lock = _threading.RLock()


def detect():
    """Enumerate CH340 candidates without opening ports or transmitting bytes.

    CH340 identifies the USB chip, not the supply model. open() verifies the model.
    """
    from serial.tools.list_ports import comports
    return [dict(port=p.device, serial_number=p.serial_number or None,
                 description=p.description)
            for p in sorted(comports(), key=lambda p: p.device)
            if (p.vid, p.pid) == (0x1A86, 0x7523)]


def open(port=None, *, timeout=1.0):
    """Open at 115200/8N1; return identity and initial settings. Queries only."""
    global _device, _owner
    with _lock:
        if _device is not None:
            raise RuntimeError('DRK already open; call close() first')
        if port is None:
            ports = detect()
            if len(ports) != 1:
                raise RuntimeError(f'found {len(ports)} CH340 candidates; specify open(port): {ports}')
            port = ports[0]['port']
        device = _Connection(port, timeout=timeout)
        try:
            info = dict(device.info)
            info['initial_settings'] = dict(info['initial_settings'])
        except BaseException:
            device.close()
            raise
        _device, _owner = device, _threading.get_ident()
        return info


def _connection():
    if _device is None:
        raise RuntimeError('DRK is not open; call open() first')
    if _owner != _threading.get_ident():
        raise RuntimeError('DRK operations must run on the thread that called open()')
    return _device


def exchange(voltage, current):
    """Set V/current limit A and receive one measurement frame atomically.

    Preserves cached output/protection bits; at most 20 control frames/s.
    No automatic write retry or independent readback of all settings.
    """
    with _lock:
        return _connection().exchange(voltage, current)


def enable():
    """Enable output, preserve setpoints/protections, then verify settings."""
    with _lock:
        return _connection().set_enabled(True)


def disable():
    """Disable and verify settings; available after an uncertain exchange."""
    with _lock:
        return _connection().set_enabled(False)


def close():
    """Close serial only; output retains its state. Safe to repeat."""
    global _device, _owner
    with _lock:
        if _device is None:
            return
        device = _connection()
        try:
            device.close()
        finally:
            _device = _owner = None
