"""Minimal INA3221 driver for bus/shunt voltage and current estimation."""

try:
    from micropython import const
except ImportError:
    def const(value):
        return value

import time

_REG_CONFIG = const(0x00)
_REG_SHUNT_BASE = const(0x01)
_REG_BUS_BASE = const(0x02)


class INA3221:
    def __init__(self, i2c, address=0x40, shunt_ohms=(0.1, 0.1, 0.1)):
        self.i2c = i2c
        self.address = address
        self.shunt_ohms = shunt_ohms
        self.configured = False
        # Enable all channels, shunt+bus continuous mode.
        try:
            self._write16(_REG_CONFIG, 0x7127)
            self.configured = True
        except Exception:
            # Some boards/devices NACK config writes but still allow register reads.
            self.configured = False

    def _read16(self, reg):
        last_exc = None
        for _ in range(3):
            try:
                data = self.i2c.readfrom_mem(self.address, reg, 2)
                return (data[0] << 8) | data[1]
            except Exception as exc:
                last_exc = exc

            try:
                self.i2c.writeto(self.address, bytes((reg,)), False)
                data = self.i2c.readfrom(self.address, 2)
                return (data[0] << 8) | data[1]
            except Exception as exc:
                last_exc = exc

            try:
                self.i2c.writeto(self.address, bytes((reg,)), True)
                time.sleep_us(50)
                data = self.i2c.readfrom(self.address, 2)
                return (data[0] << 8) | data[1]
            except Exception as exc:
                last_exc = exc

            time.sleep_ms(2)

        raise last_exc

    def _write16(self, reg, value):
        payload = bytes(((value >> 8) & 0xFF, value & 0xFF))
        last_exc = None
        for _ in range(3):
            try:
                self.i2c.writeto_mem(self.address, reg, payload)
                return
            except Exception as exc:
                last_exc = exc

            try:
                # Fallback path for ports/adapters that are flaky with writeto_mem.
                self.i2c.writeto(self.address, bytes((reg, payload[0], payload[1])))
                return
            except Exception as exc:
                last_exc = exc

            time.sleep_ms(2)

        raise last_exc

    @staticmethod
    def _to_signed(value):
        if value & 0x8000:
            value -= 0x10000
        return value

    def read_channel(self, channel):
        if channel < 1 or channel > 3:
            raise ValueError("channel must be 1..3")

        idx = channel - 1
        shunt_reg = _REG_SHUNT_BASE + (idx * 2)
        bus_reg = _REG_BUS_BASE + (idx * 2)

        raw_shunt = self._to_signed(self._read16(shunt_reg))
        raw_bus = self._read16(bus_reg)

        # LSBs from INA3221 datasheet.
        shunt_v = raw_shunt * 0.00004
        bus_v = (raw_bus >> 3) * 0.008

        r_shunt = self.shunt_ohms[idx]
        current_a = shunt_v / r_shunt if r_shunt else 0.0

        return {
            "bus_v": bus_v,
            "shunt_v": shunt_v,
            "current_a": current_a,
        }
