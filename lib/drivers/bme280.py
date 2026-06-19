"""Minimal BME280 driver for MicroPython (temperature, humidity, pressure)."""

try:
    from micropython import const
except ImportError:
    def const(value):
        return value
import time

_BME280_CHIP_ID = const(0xD0)
_BME280_RESET = const(0xE0)
_BME280_CTRL_HUM = const(0xF2)
_BME280_STATUS = const(0xF3)
_BME280_CTRL_MEAS = const(0xF4)
_BME280_CONFIG = const(0xF5)
_BME280_PRESS_MSB = const(0xF7)


class BME280:
    def __init__(self, i2c, address=0x76):
        self.i2c = i2c
        self.address = address
        self.t_fine = 0

        chip = self._read8(_BME280_CHIP_ID)
        if chip != 0x60:
            raise OSError("BME280 not found at 0x%02X" % address)

        self._write8(_BME280_RESET, 0xB6)
        time.sleep_ms(10)

        self._load_calibration()

        self._write8(_BME280_CTRL_HUM, 0x01)
        self._write8(_BME280_CTRL_MEAS, 0x27)
        self._write8(_BME280_CONFIG, 0xA0)

    def _read_mem(self, reg, nbytes):
        last_exc = None
        for _ in range(3):
            try:
                return self.i2c.readfrom_mem(self.address, reg, nbytes)
            except Exception as exc:
                last_exc = exc

            try:
                # Common register-pointer + repeated-start read.
                self.i2c.writeto(self.address, bytes((reg,)), False)
                return self.i2c.readfrom(self.address, nbytes)
            except Exception as exc:
                last_exc = exc

            try:
                # Alternate style for ports that dislike repeated-start transactions.
                self.i2c.writeto(self.address, bytes((reg,)), True)
                time.sleep_us(50)
                return self.i2c.readfrom(self.address, nbytes)
            except Exception as exc:
                last_exc = exc

            time.sleep_ms(2)

        raise last_exc

    def _write_mem(self, reg, data):
        last_exc = None
        for _ in range(3):
            try:
                self.i2c.writeto_mem(self.address, reg, data)
                return
            except Exception as exc:
                last_exc = exc

            try:
                # Fallback for ports/adapters that are flaky with writeto_mem.
                self.i2c.writeto(self.address, bytes((reg,)) + data)
                return
            except Exception as exc:
                last_exc = exc

            time.sleep_ms(2)

        raise last_exc

    def _read8(self, reg):
        return self._read_mem(reg, 1)[0]

    def _write8(self, reg, value):
        self._write_mem(reg, bytes((value,)))

    def _read16(self, reg):
        data = self._read_mem(reg, 2)
        return data[0] | (data[1] << 8)

    def _read16_signed(self, reg):
        value = self._read16(reg)
        if value & 0x8000:
            value -= 0x10000
        return value

    def _load_calibration(self):
        self.dig_t1 = self._read16(0x88)
        self.dig_t2 = self._read16_signed(0x8A)
        self.dig_t3 = self._read16_signed(0x8C)

        self.dig_p1 = self._read16(0x8E)
        self.dig_p2 = self._read16_signed(0x90)
        self.dig_p3 = self._read16_signed(0x92)
        self.dig_p4 = self._read16_signed(0x94)
        self.dig_p5 = self._read16_signed(0x96)
        self.dig_p6 = self._read16_signed(0x98)
        self.dig_p7 = self._read16_signed(0x9A)
        self.dig_p8 = self._read16_signed(0x9C)
        self.dig_p9 = self._read16_signed(0x9E)

        self.dig_h1 = self._read8(0xA1)
        self.dig_h2 = self._read16_signed(0xE1)
        self.dig_h3 = self._read8(0xE3)
        e4 = self._read8(0xE4)
        e5 = self._read8(0xE5)
        e6 = self._read8(0xE6)
        self.dig_h4 = (e4 << 4) | (e5 & 0x0F)
        if self.dig_h4 & 0x800:
            self.dig_h4 -= 0x1000
        self.dig_h5 = (e6 << 4) | (e5 >> 4)
        if self.dig_h5 & 0x800:
            self.dig_h5 -= 0x1000
        self.dig_h6 = self._read8(0xE7)
        if self.dig_h6 & 0x80:
            self.dig_h6 -= 0x100

    def _read_raw(self):
        while self._read8(_BME280_STATUS) & 0x08:
            time.sleep_ms(2)

        data = self._read_mem(_BME280_PRESS_MSB, 8)

        adc_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        adc_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        adc_h = (data[6] << 8) | data[7]
        return adc_t, adc_p, adc_h

    def read_compensated(self):
        adc_t, adc_p, adc_h = self._read_raw()

        var1 = (((adc_t >> 3) - (self.dig_t1 << 1)) * self.dig_t2) >> 11
        var2 = (((((adc_t >> 4) - self.dig_t1) * ((adc_t >> 4) - self.dig_t1)) >> 12) * self.dig_t3) >> 14
        self.t_fine = var1 + var2
        temperature = (self.t_fine * 5 + 128) >> 8

        var1 = self.t_fine - 128000
        var2 = var1 * var1 * self.dig_p6
        var2 = var2 + ((var1 * self.dig_p5) << 17)
        var2 = var2 + (self.dig_p4 << 35)
        var1 = ((var1 * var1 * self.dig_p3) >> 8) + ((var1 * self.dig_p2) << 12)
        var1 = (((1 << 47) + var1) * self.dig_p1) >> 33

        pressure = 0
        if var1 != 0:
            pressure = 1048576 - adc_p
            pressure = (((pressure << 31) - var2) * 3125) // var1
            var1 = (self.dig_p9 * (pressure >> 13) * (pressure >> 13)) >> 25
            var2 = (self.dig_p8 * pressure) >> 19
            pressure = ((pressure + var1 + var2) >> 8) + (self.dig_p7 << 4)

        humidity = self.t_fine - 76800
        humidity = (
            (((((adc_h << 14) - (self.dig_h4 << 20) - (self.dig_h5 * humidity)) + 16384) >> 15)
            * (((((((humidity * self.dig_h6) >> 10) * (((humidity * self.dig_h3) >> 11) + 32768)) >> 10) + 2097152)
            * self.dig_h2 + 8192) >> 14))
        )
        humidity = humidity - (((((humidity >> 15) * (humidity >> 15)) >> 7) * self.dig_h1) >> 4)
        if humidity < 0:
            humidity = 0
        elif humidity > 419430400:
            humidity = 419430400
        humidity = humidity >> 12

        # Temperature in C, pressure in hPa, humidity in %RH.
        return temperature / 100.0, (pressure / 256.0) / 100.0, humidity / 1024.0

    def read_dict(self):
        t, p, h = self.read_compensated()
        return {
            "temperature_c": t,
            "pressure_hpa": p,
            "humidity_pct": h,
        }
