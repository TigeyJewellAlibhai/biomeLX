"""BiomeLX startup loop.

Behavior now:
- Initialize display + drivers.
- Read available sensors when present.
- Render placeholders for missing hardware.
- Enter low-power sleep for 5 minutes.
"""

import machine
import sys
import time

# Keep a single import style by making src/ discoverable when running from tools/repl.
if "src" not in sys.path:
    sys.path.append("src")
if "/src" not in sys.path:
    sys.path.append("/src")

import config
from lib.drivers.bme280 import BME280
from lib.drivers.dual_servo import DualServo
from lib.drivers.epd_2in13_v2 import EPD2in13V2
from lib.drivers.ina3221 import INA3221
from lib.ui.status_screen import draw_status


_epd_failed_once = False


def _safe_bme(i2c, address):
    try:
        return BME280(i2c, address)
    except Exception:
        return None


def _safe_ina(i2c, address):
    try:
        return INA3221(i2c, address=address, shunt_ohms=config.INA3221_SHUNTS)
    except Exception:
        return None


def _read_bme(sensor):
    if sensor is None:
        return None, None, None
    try:
        sample = sensor.read_dict()
        return sample["temperature_c"], sample["humidity_pct"], sample["pressure_hpa"]
    except Exception:
        return None, None, None


def _read_ina_channel(sensor, channel):
    if sensor is None:
        return None, None
    try:
        sample = sensor.read_channel(channel)
        return sample["bus_v"], sample["current_a"]
    except Exception:
        return None, None


def _sleep_low_power(ms):
    # Some MicroPython targets support deepsleep(ms); fallback to lightsleep(ms).
    try:
        machine.deepsleep(ms)
    except AttributeError:
        machine.lightsleep(ms)


def _safe_epd():
    global _epd_failed_once

    if not getattr(config, "ENABLE_EPD", True):
        return None
    if _epd_failed_once:
        return None

    spi_id = getattr(config, "EPD_SPI_ID", 0)
    try:
        epd = EPD2in13V2(
            spi_id=spi_id,
            pin_sck=config.EPD_PIN_SCK,
            pin_mosi=config.EPD_PIN_MOSI,
            pin_cs=config.EPD_PIN_CS,
            pin_dc=config.EPD_PIN_DC,
            pin_rst=config.EPD_PIN_RST,
            pin_busy=config.EPD_PIN_BUSY,
            busy_active=getattr(config, "EPD_BUSY_ACTIVE_LEVEL", 1),
            hard_spi_baud=getattr(config, "EPD_HARD_SPI_BAUD", 4_000_000),
            soft_spi_baud=getattr(config, "EPD_SOFT_SPI_BAUD", 2_000_000),
            prefer_soft_spi=getattr(config, "EPD_PREFER_SOFT_SPI", False),
            rotate_180=getattr(config, "EPD_ROTATE_180", False),
        )
        epd.init()
        return epd
    except Exception as exc:
        _epd_failed_once = True
        print("EPD init failed (SPI {}): {}".format(spi_id, type(exc).__name__))
        sys.print_exception(exc)
        return None


def run_once():
    i2c = machine.I2C(
        config.I2C_ID,
        scl=machine.Pin(config.I2C_PIN_SCL),
        sda=machine.Pin(config.I2C_PIN_SDA),
        freq=config.I2C_FREQ,
    )

    internal_bme = _safe_bme(i2c, config.BME280_INTERNAL_ADDR)
    external_bme = _safe_bme(i2c, config.BME280_EXTERNAL_ADDR)
    ina = _safe_ina(i2c, config.INA3221_ADDR)

    servo = DualServo(config.SERVO_PIN_A, config.SERVO_PIN_B)
    canopy_state = "closed"
    servo.set_angle(0)

    epd = _safe_epd()

    in_t, in_h, in_p = _read_bme(internal_bme)
    out_t, out_h, out_p = _read_bme(external_bme)

    batt_v, batt_a = _read_ina_channel(ina, 1)
    solar_v, solar_a = _read_ina_channel(ina, 2)

    if epd is not None:
        draw_status(
            epd,
            {
                "state": canopy_state,
                "in_t_c": in_t,
                "in_h_pct": in_h,
                "in_p_hpa": in_p,
                "out_t_c": out_t,
                "out_h_pct": out_h,
                "out_p_hpa": out_p,
                "batt_v": batt_v,
                "batt_a": batt_a,
                "solar_v": solar_v,
                "solar_a": solar_a,
            },
        )
        epd.sleep()

    servo.deinit()


def main():
    while True:
        try:
            run_once()
        except Exception as exc:
            print("run_once failed: {}".format(exc))
            sys.print_exception(exc)
            time.sleep_ms(1000)

        if getattr(config, "DEBUG_MODE", False):
            time.sleep_ms(getattr(config, "DEBUG_LOOP_DELAY_MS", 2000))
            continue

        time.sleep_ms(200)
        if getattr(config, "ENABLE_LOW_POWER_SLEEP", True):
            _sleep_low_power(config.WAKE_INTERVAL_MS)


main()
