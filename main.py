"""Slim BiomeLX runtime.

Goals:
- Keep startup/import memory usage low.
- Preserve core behavior: sensors, canopy control, display, weather, logging,
  button actions, and compact web config UI in wireless mode.
"""

import machine
import os
import sys
import time

try:
    import builtins
except ImportError:
    builtins = None

try:
    import network
except ImportError:
    network = None

try:
    import ntptime
except ImportError:
    ntptime = None

try:
    import socket
except ImportError:
    socket = None

try:
    import urequests as requests
except ImportError:
    try:
        import requests
    except ImportError:
        requests = None

import config


SoftI2C = None
BME280 = None
INA3221 = None
DualServo = None
EPD2in13V2 = None
_draw_status = None
_draw_action_message = None
_draw_wifi_mode = None

_epd = None
_epd_failed = False
_canopy_open = False
_wireless_mode = False
_swallow_wake_press = False
_web_server = None
_web_port = None
_log_path = None


WEB_KEYS = (
    "WIFI_SSID",
    "WIFI_PASSWORD",
    "WIFI_AP_SSID",
    "WIFI_AP_PASSWORD",
    "WEB_UI_PORT",
    "WEB_UI_ALT_PORT",
    "ENABLE_WEATHER",
    "WEATHER_LATITUDE",
    "WEATHER_LONGITUDE",
    "WEATHER_RAIN_SOON_HOURS",
    "WEATHER_API_BASE_URL",
    "WEATHER_ALLOW_HTTP_FALLBACK",
    "ENABLE_WEB_TIME_SYNC",
    "NTP_HOST",
    "TIMEZONE_OFFSET_HOURS",
    "TIME_SYNC_NTP_RETRIES",
    "TIME_HTTP_FALLBACK_ENABLED",
    "TIME_HTTP_FALLBACK_URL",
    "LOG_ENABLED",
    "LOG_DIR",
    "LOG_MAX_FILES",
    "LOG_FILE_PREFIX",
    "CANOPY_SCHEDULE_ENABLED",
    "CANOPY_OPEN_TIME_HM",
    "CANOPY_CLOSE_TIME_HM",
    "CANOPY_RAIN_OVERRIDE_ENABLED",
    "CANOPY_RAIN_CLOSE_PCT",
)


def _debug_enabled():
    if builtins is not None and bool(getattr(builtins, "BIOMELX_FORCE_DEBUG", False)):
        return True
    return bool(getattr(config, "DEBUG_MODE", False))


def _dbg(msg):
    if _debug_enabled():
        print("[DBG] {}".format(msg))


def _lazy_runtime_imports():
    global SoftI2C, BME280, INA3221, DualServo, EPD2in13V2
    global _draw_status, _draw_action_message, _draw_wifi_mode

    if SoftI2C is None:
        try:
            from machine import SoftI2C as _SoftI2C
        except ImportError:
            _SoftI2C = None
        SoftI2C = _SoftI2C

    if BME280 is None:
        from lib.drivers.bme280 import BME280 as _BME280
        BME280 = _BME280
    if INA3221 is None:
        from lib.drivers.ina3221 import INA3221 as _INA3221
        INA3221 = _INA3221
    if DualServo is None:
        from lib.drivers.dual_servo import DualServo as _DualServo
        DualServo = _DualServo
    if EPD2in13V2 is None:
        from lib.drivers.epd_2in13_v2 import EPD2in13V2 as _EPD2in13V2
        EPD2in13V2 = _EPD2in13V2

    if _draw_status is None or _draw_action_message is None or _draw_wifi_mode is None:
        from lib.ui.status_screen import (
            draw_status as _s,
            draw_action_message as _a,
            draw_wifi_mode as _w,
        )
        _draw_status = _s
        _draw_action_message = _a
        _draw_wifi_mode = _w


def _safe_i2c(bus_id, scl_pin, sda_pin, freq, prefer_soft=False):
    try:
        if prefer_soft and SoftI2C is not None:
            return SoftI2C(scl=machine.Pin(scl_pin), sda=machine.Pin(sda_pin), freq=freq)
        return machine.I2C(bus_id, scl=machine.Pin(scl_pin), sda=machine.Pin(sda_pin), freq=freq)
    except Exception as exc:
        _dbg("I2C init failed id={} scl={} sda={}: {}".format(bus_id, scl_pin, sda_pin, exc))
        return None


def _safe_bme(i2c, addr):
    if i2c is None:
        return None
    try:
        return BME280(i2c, addr)
    except Exception:
        return None


def _safe_ina(i2c, addr, shunts):
    if i2c is None:
        return None
    try:
        return INA3221(i2c, address=addr, shunt_ohms=shunts)
    except Exception:
        return None


def _read_bme(sensor):
    if sensor is None:
        return None, None, None
    try:
        t, p, h = sensor.read_compensated()
        return t, h, p
    except Exception:
        return None, None, None


def _read_ina_channel(ina, channel):
    if ina is None:
        return None, None
    try:
        d = ina.read_channel(channel)
        return d.get("bus_v"), d.get("current_a")
    except Exception:
        return None, None


def _safe_epd():
    global _epd, _epd_failed

    if not bool(getattr(config, "ENABLE_EPD", True)):
        return None
    if _epd_failed:
        return None
    if _epd is not None:
        return _epd

    try:
        epd = EPD2in13V2(
            spi_id=int(getattr(config, "EPD_SPI_ID", 0)),
            pin_sck=int(getattr(config, "EPD_PIN_SCK", 18)),
            pin_mosi=int(getattr(config, "EPD_PIN_MOSI", 19)),
            pin_cs=int(getattr(config, "EPD_PIN_CS", 17)),
            pin_dc=int(getattr(config, "EPD_PIN_DC", 16)),
            pin_rst=int(getattr(config, "EPD_PIN_RST", 13)),
            pin_busy=int(getattr(config, "EPD_PIN_BUSY", 12)),
            busy_active=int(getattr(config, "EPD_BUSY_ACTIVE_LEVEL", 1)),
            hard_spi_baud=int(getattr(config, "EPD_HARD_SPI_BAUD", 4_000_000)),
            soft_spi_baud=int(getattr(config, "EPD_SOFT_SPI_BAUD", 2_000_000)),
            prefer_soft_spi=bool(getattr(config, "EPD_PREFER_SOFT_SPI", True)),
            rotate_180=bool(getattr(config, "EPD_ROTATE_180", False)),
            reverse_bits=bool(getattr(config, "EPD_REVERSE_BITS", False)),
            clear_on_init=bool(getattr(config, "EPD_CLEAR_ON_INIT", False)),
        )
        epd.init()
        _epd = epd
        return epd
    except Exception as exc:
        _epd_failed = True
        _dbg("EPD init failed: {}".format(exc))
        return None


def _safe_draw(draw_fn, *args):
    try:
        return draw_fn(*args)
    except Exception as exc:
        _dbg("draw failed: {}".format(exc))


def _local_time_tuple():
    try:
        offset_h = int(getattr(config, "TIMEZONE_OFFSET_HOURS", 0))
    except Exception:
        offset_h = 0

    try:
        return time.localtime(time.time() + (offset_h * 3600))
    except Exception:
        return time.localtime()


def _time_hm():
    lt = _local_time_tuple()
    return "{:02d}:{:02d}".format(lt[3], lt[4])


def _phase_label():
    h = _local_time_tuple()[3]
    if 5 <= h < 8:
        return "DAWN"
    if 8 <= h < 17:
        return "DAY"
    if 17 <= h < 20:
        return "DUSK"
    return "NIGHT"


def _csv_stamp():
    lt = _local_time_tuple()
    return "%04d-%02d-%02d %02d:%02d:%02d" % (lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])


def _log_fmt(v, f):
    if v is None:
        return ""
    return f % v


def _ensure_log_file():
    global _log_path

    if not bool(getattr(config, "LOG_ENABLED", True)):
        _log_path = None
        return

    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    prefix = str(getattr(config, "LOG_FILE_PREFIX", "biomelx"))
    max_files = int(getattr(config, "LOG_MAX_FILES", 4))
    if max_files < 1:
        max_files = 1

    try:
        os.mkdir(log_dir)
    except OSError:
        pass

    idx_path = "%s/.boot_index" % log_dir
    idx = -1
    try:
        with open(idx_path, "r") as fh:
            idx = int(fh.read().strip())
    except Exception:
        idx = -1

    idx = (idx + 1) % max_files
    try:
        with open(idx_path, "w") as fh:
            fh.write(str(idx))
    except Exception:
        pass

    _log_path = "%s/%s_%02d.csv" % (log_dir, prefix, idx)
    try:
        with open(_log_path, "w") as fh:
            fh.write(
                "timestamp,state,wifi_mode,wifi_connected,"
                "in_t_c,in_h_pct,in_p_hpa,out_t_c,out_h_pct,out_p_hpa,"
                "batt_v,batt_a,solar_v,solar_a,sun_pct,rain_pct,rain_3h_pct\n"
            )
    except Exception as exc:
        _dbg("log init failed: {}".format(exc))
        _log_path = None


def _append_log(payload):
    if not _log_path:
        return
    try:
        with open(_log_path, "a") as fh:
            fh.write(
                "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n".format(
                    _csv_stamp(),
                    payload.get("state", ""),
                    "1" if payload.get("wifi_mode") else "0",
                    "1" if payload.get("wifi_connected") else "0",
                    _log_fmt(payload.get("in_t_c"), "%.2f"),
                    _log_fmt(payload.get("in_h_pct"), "%.2f"),
                    _log_fmt(payload.get("in_p_hpa"), "%.2f"),
                    _log_fmt(payload.get("out_t_c"), "%.2f"),
                    _log_fmt(payload.get("out_h_pct"), "%.2f"),
                    _log_fmt(payload.get("out_p_hpa"), "%.2f"),
                    _log_fmt(payload.get("batt_v"), "%.3f"),
                    _log_fmt(payload.get("batt_a"), "%.4f"),
                    _log_fmt(payload.get("solar_v"), "%.3f"),
                    _log_fmt(payload.get("solar_a"), "%.4f"),
                    _log_fmt(payload.get("weather_sun_pct"), "%.0f"),
                    _log_fmt(payload.get("weather_rain_pct"), "%.0f"),
                    _log_fmt(payload.get("weather_rain_3h_pct"), "%.0f"),
                )
            )
    except Exception:
        pass


def _load_canopy_state():
    default_open = bool(getattr(config, "SERVO_DEFAULT_OPEN", False))
    path = str(getattr(config, "SERVO_STATE_FILE", "canopy_state.txt"))
    try:
        with open(path, "r") as fh:
            raw = fh.read().strip().lower()
        if raw in ("1", "true", "open"):
            return True
        if raw in ("0", "false", "closed", "close"):
            return False
    except Exception:
        pass
    return default_open


def _save_canopy_state(is_open):
    path = str(getattr(config, "SERVO_STATE_FILE", "canopy_state.txt"))
    try:
        with open(path, "w") as fh:
            fh.write("open" if is_open else "closed")
    except Exception:
        pass


def _parse_hm_minutes(text):
    try:
        s = str(text).strip()
        parts = s.split(":")
        if len(parts) != 2:
            return None
        hh = int(parts[0])
        mm = int(parts[1])
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return None
        return hh * 60 + mm
    except Exception:
        return None


def _is_scheduled_open_now():
    open_m = _parse_hm_minutes(getattr(config, "CANOPY_OPEN_TIME_HM", "07:00"))
    close_m = _parse_hm_minutes(getattr(config, "CANOPY_CLOSE_TIME_HM", "20:00"))
    if open_m is None or close_m is None:
        return None

    lt = _local_time_tuple()
    now_m = lt[3] * 60 + lt[4]

    if open_m == close_m:
        return True
    if open_m < close_m:
        return (now_m >= open_m) and (now_m < close_m)
    return (now_m >= open_m) or (now_m < close_m)


def _effective_rain(now_pct, soon_pct):
    vals = []
    if isinstance(now_pct, (int, float)):
        vals.append(float(now_pct))
    if isinstance(soon_pct, (int, float)):
        vals.append(float(soon_pct))
    if not vals:
        return None
    return max(vals)


def _apply_canopy_rules(rain_now_pct, rain_3h_pct):
    desired = _canopy_open

    if bool(getattr(config, "CANOPY_SCHEDULE_ENABLED", False)):
        sch = _is_scheduled_open_now()
        if sch is not None:
            desired = sch

    if bool(getattr(config, "CANOPY_RAIN_OVERRIDE_ENABLED", False)):
        thr = int(getattr(config, "CANOPY_RAIN_CLOSE_PCT", 70))
        if thr < 0:
            thr = 0
        if thr > 100:
            thr = 100
        eff = _effective_rain(rain_now_pct, rain_3h_pct)
        if eff is not None and eff >= thr:
            desired = False

    return desired


def _set_canopy_state(servo, should_open, force=False):
    global _canopy_open

    target = bool(should_open)
    if (not force) and (_canopy_open == target):
        return

    target_angle = int(getattr(config, "SERVO_OPEN_ANGLE", 40) if target else getattr(config, "SERVO_CLOSED_ANGLE", 270))
    mode = str(getattr(config, "SERVO_MOTION_MODE", "ramped")).strip().lower()

    if mode == "simple":
        servo.set_angle(target_angle)
    else:
        servo.move_angle(
            target_angle,
            total_ms=int(getattr(config, "SERVO_MOVE_TOTAL_MS", 8000)),
            step_ms=int(getattr(config, "SERVO_MOVE_STEP_MS", 35)),
            ramp_strength=float(getattr(config, "SERVO_RAMP_STRENGTH", 0.12)),
            breakaway_deg=float(getattr(config, "SERVO_BREAKAWAY_DEG", 6.0)),
            breakaway_hold_ms=int(getattr(config, "SERVO_BREAKAWAY_HOLD_MS", 120)),
            min_step_deg=float(getattr(config, "SERVO_MIN_STEP_DEG", 1.5)),
        )

    _canopy_open = target
    _save_canopy_state(_canopy_open)


def _wifi_connect(timeout_ms=None):
    if network is None:
        return None

    ssid = str(getattr(config, "WIFI_SSID", "")).strip()
    password = str(getattr(config, "WIFI_PASSWORD", ""))
    if not ssid:
        return None

    if timeout_ms is None:
        timeout_ms = int(getattr(config, "WIFI_CONNECT_TIMEOUT_MS", 30_000))

    attempts = int(getattr(config, "WIFI_CONNECT_ATTEMPTS", 2))
    if attempts < 1:
        attempts = 1

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    for _ in range(attempts):
        try:
            wlan.disconnect()
        except Exception:
            pass

        try:
            wlan.connect(ssid, password)
        except Exception:
            pass

        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            try:
                if wlan.isconnected():
                    return wlan
            except Exception:
                pass
            time.sleep_ms(120)

    return None


def _wifi_start_ap():
    if network is None:
        return None

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ssid = str(getattr(config, "WIFI_AP_SSID", "BiomeLX-Setup"))
    pwd = str(getattr(config, "WIFI_AP_PASSWORD", ""))
    try:
        if pwd and len(pwd) >= 8:
            try:
                ap.config(essid=ssid, password=pwd, authmode=3)
            except Exception:
                ap.config(essid=ssid, password=pwd)
        else:
            ap.config(essid=ssid)
    except Exception:
        pass
    return ap


def _wifi_disable_all():
    if network is None:
        return
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(False)
    except Exception:
        pass
    try:
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
    except Exception:
        pass


def _wifi_is_connected():
    if network is None:
        return False
    try:
        sta = network.WLAN(network.STA_IF)
        return bool(sta.active() and sta.isconnected())
    except Exception:
        return False


def _sync_time_from_web():
    if not bool(getattr(config, "ENABLE_WEB_TIME_SYNC", True)):
        return False

    if not _wifi_is_connected():
        return False

    # NTP first.
    if ntptime is not None:
        retries = int(getattr(config, "TIME_SYNC_NTP_RETRIES", 2))
        if retries < 1:
            retries = 1
        host = str(getattr(config, "NTP_HOST", "pool.ntp.org")).strip()
        if host:
            try:
                ntptime.host = host
            except Exception:
                pass
        for _ in range(retries):
            try:
                ntptime.settime()
                return True
            except Exception:
                time.sleep_ms(200)

    # HTTP fallback.
    if not bool(getattr(config, "TIME_HTTP_FALLBACK_ENABLED", True)):
        return False
    if requests is None:
        return False

    url = str(getattr(config, "TIME_HTTP_FALLBACK_URL", "http://worldtimeapi.org/api/timezone/Etc/UTC"))
    resp = None
    try:
        resp = requests.get(url)
        if getattr(resp, "status_code", 0) != 200:
            return False
        data = resp.json()
        unix = data.get("unixtime") if isinstance(data, dict) else None
        if not isinstance(unix, int):
            return False
        # Year/day values are accepted from RTC once set by epoch conversion path.
        time.localtime(unix)
        return True
    except Exception:
        return False
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def _is_rain_code(code):
    if code is None:
        return False
    try:
        c = int(code)
    except Exception:
        return False
    return ((51 <= c <= 67) or (80 <= c <= 82) or (95 <= c <= 99))


def _read_weather():
    if not bool(getattr(config, "ENABLE_WEATHER", True)):
        return None
    if requests is None:
        return None

    lat = getattr(config, "WEATHER_LATITUDE", None)
    lon = getattr(config, "WEATHER_LONGITUDE", None)
    if lat is None or lon is None:
        return None

    # Ensure we have Wi-Fi for pull mode.
    wlan = _wifi_connect(timeout_ms=int(getattr(config, "WIFI_PULL_CONNECT_TIMEOUT_MS", 12_000)))
    if wlan is None:
        return None

    _sync_time_from_web()

    soon_h = int(getattr(config, "WEATHER_RAIN_SOON_HOURS", 3))
    if soon_h < 1:
        soon_h = 1
    if soon_h > 6:
        soon_h = 6

    base = str(getattr(config, "WEATHER_API_BASE_URL", "https://api.open-meteo.com/v1/forecast"))
    url = (
        "{base}?latitude={lat}&longitude={lon}"
        "&current=cloud_cover,precipitation,rain,weather_code"
        "&hourly=precipitation_probability&forecast_hours={hours}&timezone=auto"
    ).format(base=base, lat=lat, lon=lon, hours=max(soon_h, 3))

    resp = None
    try:
        urls = [url]
        if bool(getattr(config, "WEATHER_ALLOW_HTTP_FALLBACK", True)) and url.startswith("https://"):
            urls.append("http://" + url[len("https://"):])

        last = None
        for try_url in urls:
            try:
                resp = requests.get(try_url)
                break
            except Exception as exc:
                last = exc
                resp = None
        if resp is None:
            if last is not None:
                _dbg("weather get failed: {}".format(last))
            return None

        if getattr(resp, "status_code", 0) != 200:
            return None

        data = resp.json()
        if not isinstance(data, dict):
            return None

        cur = data.get("current", {})
        cloud = cur.get("cloud_cover")
        rain_mm = cur.get("rain")
        pnow = cur.get("precipitation")
        wcode = cur.get("weather_code")

        sun_pct = None
        if isinstance(cloud, (int, float)):
            sun_pct = max(0, min(100, int(100 - cloud)))

        raining = False
        if isinstance(rain_mm, (int, float)) and rain_mm > 0:
            raining = True
        if isinstance(pnow, (int, float)) and pnow > 0:
            raining = True
        if _is_rain_code(wcode):
            raining = True

        rain_now_pct = None
        rain_3h_pct = None
        hourly = data.get("hourly", {})
        probs = hourly.get("precipitation_probability") if isinstance(hourly, dict) else None
        if isinstance(probs, list):
            if probs and isinstance(probs[0], (int, float)):
                rain_now_pct = int(probs[0])
            vals = []
            for i, val in enumerate(probs):
                if i >= 3:
                    break
                if isinstance(val, (int, float)):
                    vals.append(val)
            if vals:
                rain_3h_pct = int(max(vals))

        if rain_now_pct is None and raining:
            rain_now_pct = 100

        return {
            "sun_pct": sun_pct,
            "rain_now_pct": rain_now_pct,
            "rain_3h_pct": rain_3h_pct,
        }
    except Exception as exc:
        _dbg("weather failed: {}".format(exc))
        return None
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def _url_decode(s):
    s = s.replace("+", " ")
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "%" and i + 2 < n:
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def _html_escape(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _load_config_vars():
    path = "config.py"
    out = {}
    try:
        with open(path, "r") as fh:
            lines = fh.readlines()
    except Exception:
        return path, [], out

    for line in lines:
        stripped = line.strip()
        if (not stripped) or stripped.startswith("#") or ("=" not in stripped):
            continue
        left, right = stripped.split("=", 1)
        key = left.strip()
        if key in WEB_KEYS:
            out[key] = right.strip()

    for key in WEB_KEYS:
        if key not in out and hasattr(config, key):
            out[key] = repr(getattr(config, key))

    return path, lines, out


def _coerce_literal(new_text, old_literal):
    txt = str(new_text).strip()
    old = str(old_literal).strip()

    if old in ("True", "False"):
        low = txt.lower()
        if low == "true":
            return "True"
        if low == "false":
            return "False"

    if len(old) >= 2 and old[0] == old[-1] and old[0] in ('"', "'"):
        q = old[0]
        if len(txt) >= 2 and txt[0] == txt[-1] and txt[0] in ('"', "'"):
            return txt
        esc = txt.replace("\\", "\\\\")
        if q == '"':
            esc = esc.replace('"', '\\"')
        else:
            esc = esc.replace("'", "\\'")
        return q + esc + q

    return txt


def _save_config_vars(changes):
    path, lines, _ = _load_config_vars()
    if not lines:
        return False

    found = set()
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (not stripped) or stripped.startswith("#") or ("=" not in stripped):
            continue
        left, _ = stripped.split("=", 1)
        key = left.strip()
        found.add(key)
        if key in changes:
            new_line = "%s = %s\n" % (key, changes[key])
            if lines[i] != new_line:
                lines[i] = new_line
                changed = True

    for key, val in changes.items():
        if key not in found:
            lines.append("%s = %s\n" % (key, val))
            changed = True

    if not changed:
        return False

    tmp = path + ".tmp"
    bak = path + ".bak"

    try:
        with open(tmp, "w") as fh:
            fh.write("".join(lines))
        try:
            os.remove(bak)
        except Exception:
            pass
        try:
            os.rename(path, bak)
        except Exception:
            pass
        os.rename(tmp, path)
        return True
    except Exception as exc:
        _dbg("save config failed: {}".format(exc))
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def _http_send(client, status, ctype, body, extra_headers=None):
    if isinstance(body, str):
        body = body.encode("utf-8")
    if extra_headers is None:
        extra_headers = []

    hdr = [
        "HTTP/1.1 %s\r\n" % status,
        "Content-Type: %s\r\n" % ctype,
        "Content-Length: %d\r\n" % len(body),
        "Connection: close\r\n",
    ]
    for h in extra_headers:
        hdr.append(h + "\r\n")
    hdr.append("\r\n")

    client.send("".join(hdr).encode("utf-8"))
    if body:
        client.send(body)


def _parse_form(body):
    out = {}
    parts = body.split("&")
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        key = _url_decode(k)
        val = _url_decode(v)
        if key:
            out[key] = val
    return out


def _render_index():
    _, _, vars_map = _load_config_vars()

    sections = {
        "Network": [],
        "Canopy + Rain": [],
        "Weather": [],
        "Time": [],
        "Logging": [],
    }

    for key in vars_map:
        if key.startswith("WIFI_") or key.startswith("WEB_UI_"):
            sections["Network"].append(key)
        elif key.startswith("CANOPY_") or key == "WEATHER_RAIN_SOON_HOURS":
            sections["Canopy + Rain"].append(key)
        elif key.startswith("WEATHER_") or key == "ENABLE_WEATHER":
            sections["Weather"].append(key)
        elif key.startswith("TIME_") or key in ("ENABLE_WEB_TIME_SYNC", "NTP_HOST", "TIMEZONE_OFFSET_HOURS"):
            sections["Time"].append(key)
        else:
            sections["Logging"].append(key)

    parts = []
    parts.append("<html><head><title>BiomeLX</title>")
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append("<style>body{font:14px sans-serif;margin:10px}.row{margin:6px 0}.row label{display:block;font-size:12px}.row input{width:100%;padding:4px}fieldset{margin:10px 0}</style>")
    parts.append("</head><body><h1>BiomeLX Web UI</h1>")
    parts.append('<form method="POST" action="/save">')

    for sec in ("Network", "Canopy + Rain", "Weather", "Time", "Logging"):
        keys = sections[sec]
        if not keys:
            continue
        parts.append("<fieldset><legend>%s</legend>" % _html_escape(sec))
        for key in keys:
            parts.append(
                '<div class="row"><label for="%s">%s</label><input id="%s" name="%s" value="%s"></div>'
                % (
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(vars_map.get(key, "")),
                )
            )
        parts.append("</fieldset>")

    parts.append('<button type="submit">Save Config</button>')
    parts.append(' <a href="/logs">Download Logs</a>')
    parts.append("</form>")
    parts.append('<form method="POST" action="/reset" style="margin-top:10px"><button type="submit">Hard Reset</button></form>')
    parts.append("</body></html>")
    return "".join(parts)


def _render_logs_page():
    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    names = []
    try:
        names = sorted(os.listdir(log_dir))
    except Exception:
        pass

    links = []
    for n in names:
        if n.endswith(".csv"):
            links.append('<li><a href="/logs/%s">%s</a></li>' % (_html_escape(n), _html_escape(n)))

    return (
        "<html><body><h1>Logs</h1><ul>%s</ul><p><a href=\"/\">Back</a></p></body></html>"
        % "".join(links)
    )


def _serve_log(client, name):
    if (not name) or ("/" in name) or (".." in name) or ("\\" in name):
        _http_send(client, "400 Bad Request", "text/plain", "Invalid filename")
        return

    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    path = "%s/%s" % (log_dir, name)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        _http_send(
            client,
            "200 OK",
            "text/csv",
            data,
            extra_headers=["Content-Disposition: attachment; filename=%s" % name],
        )
    except Exception:
        _http_send(client, "404 Not Found", "text/plain", "Log not found")


def _handle_http(client, method, path, body):
    clean = path.split("?", 1)[0]

    if method == "GET" and clean == "/":
        _http_send(client, "200 OK", "text/html; charset=utf-8", _render_index())
        return

    if method == "GET" and clean == "/logs":
        _http_send(client, "200 OK", "text/html; charset=utf-8", _render_logs_page())
        return

    if method == "GET" and clean.startswith("/logs/"):
        _serve_log(client, clean[len("/logs/"):])
        return

    if method == "POST" and clean == "/save":
        posted = _parse_form(body)
        _, _, existing = _load_config_vars()
        changes = {}
        for key, old_literal in existing.items():
            if key not in posted:
                continue
            val = posted[key]
            if key in ("WIFI_SSID", "WIFI_PASSWORD") and not str(val).strip():
                continue
            new_literal = _coerce_literal(val, old_literal)
            if new_literal != str(old_literal).strip():
                changes[key] = new_literal

        _save_config_vars(changes)
        _http_send(client, "303 See Other", "text/html", "<html><body>Saved. <a href='/' >Back</a></body></html>", extra_headers=["Location: /"])
        return

    if method == "POST" and clean == "/reset":
        _http_send(client, "200 OK", "text/html", "<html><body><h1>Rebooting...</h1></body></html>")
        time.sleep_ms(150)
        machine.reset()
        return

    _http_send(client, "404 Not Found", "text/plain", "Not found")


def _start_web_server():
    global _web_server, _web_port

    if socket is None:
        return False

    _stop_web_server()

    p1 = int(getattr(config, "WEB_UI_PORT", 80))
    p2 = int(getattr(config, "WEB_UI_ALT_PORT", 8080))
    ports = (p1,) if p1 == p2 else (p1, p2)

    for port in ports:
        s = None
        try:
            s = socket.socket()
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
            s.bind(addr)
            s.listen(2)
            s.settimeout(0.05)
            _web_server = s
            _web_port = port
            return True
        except Exception:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    return False


def _stop_web_server():
    global _web_server, _web_port
    if _web_server is not None:
        try:
            _web_server.close()
        except Exception:
            pass
    _web_server = None
    _web_port = None


def _poll_web_server_once():
    if _web_server is None:
        return

    client = None
    try:
        client, _ = _web_server.accept()
        timeout_s = float(getattr(config, "WEB_UI_CLIENT_TIMEOUT_S", 1.5))
        if timeout_s < 0.1:
            timeout_s = 0.1
        client.settimeout(timeout_s)

        req = bytearray()
        while (b"\r\n\r\n" not in req) and (len(req) < 2048):
            chunk = client.recv(256)
            if not chunk:
                break
            req.extend(chunk)

        if not req:
            return

        parts = bytes(req).split(b"\r\n\r\n", 1)
        header_blob = parts[0]
        body_blob = parts[1] if len(parts) > 1 else b""

        lines = header_blob.decode("utf-8", "ignore").split("\r\n")
        first = lines[0].split(" ") if lines else []
        if len(first) < 2:
            _http_send(client, "400 Bad Request", "text/plain", "Malformed request")
            return

        method = first[0].upper()
        path = first[1]

        clen = 0
        for line in lines[1:]:
            low = line.lower()
            if low.startswith("content-length:"):
                try:
                    clen = int(line.split(":", 1)[1].strip())
                except Exception:
                    clen = 0
                break

        while len(body_blob) < clen:
            chunk = client.recv(min(256, clen - len(body_blob)))
            if not chunk:
                break
            body_blob += chunk

        body = body_blob.decode("utf-8", "ignore") if body_blob else ""
        _handle_http(client, method, path, body)
    except Exception:
        try:
            if client is not None:
                _http_send(client, "500 Internal Server Error", "text/plain", "Internal error")
        except Exception:
            pass
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _button(pin):
    return pin.value() == 0


def _enter_wireless_mode(epd, payload):
    if epd is not None:
        _safe_draw(_draw_wifi_mode, epd, "CONNECTING", "Station", "", "quick")

    wlan = _wifi_connect(timeout_ms=int(getattr(config, "WIFI_CONNECT_TIMEOUT_MS", 30_000)))
    if wlan is not None:
        ip = "?"
        try:
            ip = wlan.ifconfig()[0]
        except Exception:
            pass
        _sync_time_from_web()
        _start_web_server()
        payload["wifi_connected"] = True
        payload["wifi_mode"] = True
        if epd is not None:
            _safe_draw(_draw_wifi_mode, epd, "CONNECTED", "WEB:{}".format(_web_port or "OFF"), ip, "quick")
        return epd

    ap = _wifi_start_ap()
    ip = "?"
    if ap is not None:
        try:
            ip = ap.ifconfig()[0]
        except Exception:
            pass
    _start_web_server()
    payload["wifi_connected"] = False
    payload["wifi_mode"] = True
    if epd is not None:
        _safe_draw(_draw_wifi_mode, epd, "AP MODE", "WEB:{}".format(_web_port or "OFF"), ip, "quick")
    return epd


def _exit_wireless_mode(epd, payload):
    _stop_web_server()
    _wifi_disable_all()
    payload["wifi_connected"] = False
    payload["wifi_mode"] = False
    if epd is not None:
        _safe_draw(_draw_action_message, epd, "WIRELESS", "OFF", "quick")
    return epd


def _prepare_payload(in_t, in_h, in_p, out_t, out_h, out_p, batt_v, batt_a, solar_v, solar_a, weather):
    weather_sun = None
    weather_rain = None
    weather_rain3 = None
    if isinstance(weather, dict):
        weather_sun = weather.get("sun_pct")
        weather_rain = weather.get("rain_now_pct")
        weather_rain3 = weather.get("rain_3h_pct")

    return {
        "state": "open" if _canopy_open else "closed",
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
        "weather_sun_pct": weather_sun,
        "weather_rain_pct": weather_rain,
        "weather_rain_3h_pct": weather_rain3,
        "time_hm": _time_hm(),
        "last_update_hm": _time_hm(),
        "weather_phase": _phase_label(),
        "wifi_connected": _wifi_is_connected(),
        "wifi_mode": _wireless_mode,
    }


def run_once():
    global _canopy_open, _wireless_mode, _swallow_wake_press

    _lazy_runtime_imports()
    _canopy_open = _load_canopy_state()

    bme_i2c = _safe_i2c(
        int(getattr(config, "BME_I2C_ID", getattr(config, "I2C_ID", 0))),
        int(getattr(config, "BME_I2C_PIN_SCL", getattr(config, "I2C_PIN_SCL", 1))),
        int(getattr(config, "BME_I2C_PIN_SDA", getattr(config, "I2C_PIN_SDA", 0))),
        int(getattr(config, "BME_I2C_FREQ", getattr(config, "I2C_FREQ", 50_000))),
        prefer_soft=bool(getattr(config, "BME_PREFER_SOFT_I2C", True)),
    )

    ina_i2c = _safe_i2c(
        int(getattr(config, "INA_I2C_ID", 0)),
        int(getattr(config, "INA_I2C_PIN_SCL", 5)),
        int(getattr(config, "INA_I2C_PIN_SDA", 4)),
        int(getattr(config, "INA_I2C_FREQ", 50_000)),
        prefer_soft=bool(getattr(config, "INA_PREFER_SOFT_I2C", True)),
    )

    internal = _safe_bme(bme_i2c, int(getattr(config, "BME280_INTERNAL_ADDR", 0x77)))
    external = _safe_bme(bme_i2c, int(getattr(config, "BME280_EXTERNAL_ADDR", 0x76)))
    ina = _safe_ina(ina_i2c, int(getattr(config, "INA3221_ADDR", 0x40)), getattr(config, "INA3221_SHUNTS", (0.1, 0.1, 0.1)))

    in_t, in_h, in_p = _read_bme(internal)
    out_t, out_h, out_p = _read_bme(external)

    batt_ch = int(getattr(config, "INA_BATT_CHANNEL", 1))
    solar_ch = int(getattr(config, "INA_SOLAR_CHANNEL", 2))
    batt_v, batt_a = _read_ina_channel(ina, batt_ch)
    solar_v, solar_a = _read_ina_channel(ina, solar_ch)

    if isinstance(batt_v, (int, float)):
        batt_v *= float(getattr(config, "INA_BATT_VOLTAGE_SCALE", 1.0))
    if isinstance(solar_v, (int, float)):
        solar_v *= float(getattr(config, "INA_SOLAR_VOLTAGE_SCALE", 1.0))

    servo = DualServo(
        int(getattr(config, "SERVO_PIN_A", 14)),
        int(getattr(config, "SERVO_PIN_B", 15)),
        freq=int(getattr(config, "SERVO_PWM_FREQ", 50)),
        min_us=int(getattr(config, "SERVO_MIN_PULSE_US", 600)),
        max_us=int(getattr(config, "SERVO_MAX_PULSE_US", 2400)),
        span_deg=int(getattr(config, "SERVO_SPAN_DEG", 270)),
    )

    weather = _read_weather()
    rain_now = weather.get("rain_now_pct") if isinstance(weather, dict) else None
    rain_3h = weather.get("rain_3h_pct") if isinstance(weather, dict) else None
    desired = _apply_canopy_rules(rain_now, rain_3h)
    _set_canopy_state(servo, desired, force=True)

    payload = _prepare_payload(in_t, in_h, in_p, out_t, out_h, out_p, batt_v, batt_a, solar_v, solar_a, weather)
    payload["state"] = "open" if _canopy_open else "closed"

    _append_log(payload)

    epd = _safe_epd()
    if epd is not None:
        _safe_draw(_draw_status, epd, payload, "quick" if bool(getattr(config, "EPD_ENABLE_QUICK_REFRESH", False)) else "full")

    pin_mode = machine.Pin(int(getattr(config, "BUTTON_MODE_PIN", 8)), machine.Pin.IN, machine.Pin.PULL_UP)
    pin_servo = machine.Pin(int(getattr(config, "BUTTON_SERVO_PIN", 9)), machine.Pin.IN, machine.Pin.PULL_UP)
    debounce = int(getattr(config, "BUTTON_DEBOUNCE_MS", 250))
    action_ms = int(getattr(config, "BUTTON_ACTION_DISPLAY_MS", 5000))
    idle_ms = int(getattr(config, "AWAKE_IDLE_TIMEOUT_MS", 60_000))

    if _swallow_wake_press:
        while _button(pin_mode) or _button(pin_servo):
            time.sleep_ms(20)
        _swallow_wake_press = False

    last_activity = time.ticks_ms()
    last_mode_press = 0
    last_servo_press = 0
    prev_mode = False
    prev_servo = False
    action_until = None

    if _wireless_mode:
        epd = _enter_wireless_mode(epd, payload)

    while True:
        now = time.ticks_ms()
        payload["time_hm"] = _time_hm()
        payload["weather_phase"] = _phase_label()

        if (not _wireless_mode) and time.ticks_diff(now, last_activity) >= idle_ms:
            break

        if _wireless_mode:
            _poll_web_server_once()

        mode_pressed = _button(pin_mode)
        servo_pressed = _button(pin_servo)
        mode_edge = mode_pressed and (not prev_mode)
        servo_edge = servo_pressed and (not prev_servo)
        prev_mode = mode_pressed
        prev_servo = servo_pressed

        if mode_edge and time.ticks_diff(now, last_mode_press) >= debounce:
            last_mode_press = now
            last_activity = now
            _wireless_mode = not _wireless_mode
            payload["wifi_mode"] = _wireless_mode
            if _wireless_mode:
                epd = _enter_wireless_mode(epd, payload)
                action_until = None
            else:
                epd = _exit_wireless_mode(epd, payload)
                action_until = time.ticks_add(now, action_ms)

        if servo_edge and time.ticks_diff(now, last_servo_press) >= debounce:
            last_servo_press = now
            last_activity = now
            next_open = not _canopy_open
            if epd is not None:
                _safe_draw(_draw_action_message, epd, "OPENING" if next_open else "CLOSING", "", "quick")
            _set_canopy_state(servo, next_open)
            payload["state"] = "open" if _canopy_open else "closed"
            payload["last_update_hm"] = _time_hm()
            if epd is not None and (not _wireless_mode):
                _safe_draw(_draw_status, epd, payload, "quick")
            action_until = None

        if _wireless_mode:
            payload["wifi_connected"] = _wifi_is_connected()

        if epd is not None and (not _wireless_mode) and action_until is not None and time.ticks_diff(now, action_until) >= 0:
            _safe_draw(_draw_status, epd, payload, "quick")
            action_until = None

        time.sleep_ms(50)

    _stop_web_server()

    if epd is not None and (not _debug_enabled()):
        try:
            epd.sleep()
        except Exception:
            pass

    servo.deinit()


def _sleep_until_event(total_ms):
    pin_mode = machine.Pin(int(getattr(config, "BUTTON_MODE_PIN", 8)), machine.Pin.IN, machine.Pin.PULL_UP)
    pin_servo = machine.Pin(int(getattr(config, "BUTTON_SERVO_PIN", 9)), machine.Pin.IN, machine.Pin.PULL_UP)

    poll_ms = int(getattr(config, "SLEEP_POLL_MS", 1000))
    if poll_ms < 50:
        poll_ms = 50

    end_ms = time.ticks_add(time.ticks_ms(), total_ms)
    while time.ticks_diff(end_ms, time.ticks_ms()) > 0:
        if bool(getattr(config, "ENABLE_BUTTON_WAKE", True)) and (_button(pin_mode) or _button(pin_servo)):
            return "button"
        rem = time.ticks_diff(end_ms, time.ticks_ms())
        step = poll_ms if rem > poll_ms else rem
        if step > 0:
            time.sleep_ms(step)
    return "timer"


def main():
    global _last_wake_reason, _swallow_wake_press

    _ensure_log_file()

    while True:
        try:
            run_once()
        except Exception as exc:
            print("run_once failed: {}".format(exc))
            sys.print_exception(exc)
            time.sleep_ms(1000)

        if _debug_enabled():
            time.sleep_ms(int(getattr(config, "DEBUG_LOOP_DELAY_MS", 20_000)))
            continue

        if _wireless_mode:
            time.sleep_ms(200)
            continue

        wake_ms = int(getattr(config, "STANDARD_WAKE_INTERVAL_MS", getattr(config, "WAKE_INTERVAL_MS", 10 * 60 * 1000)))
        reason = _sleep_until_event(wake_ms)
        _last_wake_reason = reason
        if reason == "button":
            _swallow_wake_press = True


main()
