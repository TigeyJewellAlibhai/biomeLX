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
import os

try:
    import socket
except ImportError:
    socket = None

try:
    import ntptime
except ImportError:
    ntptime = None

try:
    import builtins
except ImportError:
    builtins = None

try:
    import network
except ImportError:
    network = None

try:
    import urequests as requests
except ImportError:
    try:
        import requests
    except ImportError:
        requests = None

import config
from lib.drivers.bme280 import BME280
from lib.drivers.dual_servo import DualServo
from lib.drivers.epd_2in13_v2 import EPD2in13V2
from lib.drivers.ina3221 import INA3221
from lib.ui.status_screen import draw_action_message, draw_status, draw_wifi_mode


_epd_failed_once = False
_epd_cached = None
_power_saver_mode = False
_canopy_open = False
_swallow_wake_button_press = False
_button_wake_flag = False
_last_wake_reason = "timer"
_wireless_mode = False
_web_server = None
_web_server_port = None
_log_path = None
_servo_rail_pin = None
_servo_rail_init_failed = False

_REQUIRED_CONFIG_DEFAULTS = {
    "BME280_INTERNAL_ADDR": 0x77,
    "BME280_EXTERNAL_ADDR": 0x76,
    "INA3221_SHUNTS": (0.1, 0.1, 0.1),
    "SERVO_PIN_A": 14,
    "SERVO_PIN_B": 15,
    "SERVO_OPEN_ANGLE": 0,
    "SERVO_CLOSED_ANGLE": 220,
    "SERVO_PWM_FREQ": 50,
    "SERVO_MIN_PULSE_US": 1000,
    "SERVO_MAX_PULSE_US": 2000,
    "SERVO_SPAN_DEG": 180,
    "SERVO_STATE_FILE": "canopy_state.txt",
    "SERVO_DEFAULT_OPEN": False,
    "CANOPY_SCHEDULE_ENABLED": False,
    "CANOPY_OPEN_TIME_HM": "07:00",
    "CANOPY_CLOSE_TIME_HM": "20:00",
    "CANOPY_RAIN_OVERRIDE_ENABLED": False,
    "CANOPY_RAIN_CLOSE_PCT": 70,
    "TIME_SYNC_NTP_RETRIES": 3,
    "TIME_HTTP_FALLBACK_ENABLED": True,
    "TIME_HTTP_FALLBACK_URL": "http://worldtimeapi.org/api/timezone/Etc/UTC",
    "TIME_SYNC_MIN_VALID_YEAR": 2024,
    "SERVO_RAIL_ENABLE_PIN": 2,
    "SERVO_RAIL_ACTIVE_HIGH": True,
    "SERVO_RAIL_SETTLE_MS": 80,
    "SERVO_RAIL_OFF_DELAY_MS": 80,
    "EPD_PIN_SCK": 18,
    "EPD_PIN_MOSI": 19,
    "EPD_PIN_CS": 17,
    "EPD_PIN_DC": 16,
    "EPD_PIN_RST": 13,
    "EPD_PIN_BUSY": 12,
}

_CRITICAL_CONFIG_KEYS = tuple(_REQUIRED_CONFIG_DEFAULTS.keys())

try:
    from machine import SoftI2C
except ImportError:
    SoftI2C = None


def _debug_enabled():
    if builtins is not None and bool(getattr(builtins, "BIOMELX_FORCE_DEBUG", False)):
        return True
    return bool(getattr(config, "DEBUG_MODE", False))


def _dbg(msg):
    if _debug_enabled():
        print("[DBG] {}".format(msg))


def _web_debug_enabled():
    return bool(getattr(config, "WEB_UI_DEBUG", True))


def _web_log(msg):
    if _web_debug_enabled():
        print("[WEB] {}".format(msg))


def _is_socket_timeout(exc):
    try:
        code = exc.args[0]
        if code in (110,):
            return True
    except Exception:
        pass

    text = str(exc).lower()
    return ("timed out" in text) or ("etimedout" in text)


def _wake_interval_ms():
    if _power_saver_mode:
        return int(getattr(config, "POWER_SAVER_WAKE_INTERVAL_MS", 60 * 60 * 1000))
    return int(getattr(config, "STANDARD_WAKE_INTERVAL_MS", getattr(config, "WAKE_INTERVAL_MS", 10 * 60 * 1000)))


def _weather_phase_label():
    # Local-hour approximation for dawn/day/dusk/night bucket.
    hour = time.localtime()[3]
    if 5 <= hour < 8:
        return "DAWN"
    if 8 <= hour < 17:
        return "DAY"
    if 17 <= hour < 20:
        return "DUSK"
    return "NIGHT"


def _local_time_hm():
    lt = time.localtime()
    return "{:02d}:{:02d}".format(lt[3], lt[4])


def _csv_time_stamp():
    lt = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        lt[0], lt[1], lt[2], lt[3], lt[4], lt[5]
    )


def _log_fmt(value, fmt):
    if value is None:
        return ""
    return fmt % value


def _ensure_log_file():
    global _log_path

    if not bool(getattr(config, "LOG_ENABLED", True)):
        _log_path = None
        return None

    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    prefix = str(getattr(config, "LOG_FILE_PREFIX", "biomelx"))
    max_files = int(getattr(config, "LOG_MAX_FILES", 4))
    if max_files < 1:
        max_files = 1

    try:
        os.mkdir(log_dir)
    except OSError:
        pass

    index_path = "{}/.boot_index".format(log_dir)
    boot_index = -1
    try:
        with open(index_path, "r") as fh:
            boot_index = int(fh.read().strip())
    except Exception:
        boot_index = -1

    boot_index = (boot_index + 1) % max_files
    try:
        with open(index_path, "w") as fh:
            fh.write(str(boot_index))
    except Exception as exc:
        _dbg("Failed to update log index: {}".format(exc))

    _log_path = "{}/{}_{:02d}.csv".format(log_dir, prefix, boot_index)
    try:
        with open(_log_path, "w") as fh:
            fh.write(
                "timestamp,state,wifi_mode,wifi_connected,"
                "in_t_c,in_h_pct,in_p_hpa,out_t_c,out_h_pct,out_p_hpa,"
                "batt_v,batt_a,solar_v,solar_a,sun_pct,rain_pct,rain_3h_pct\n"
            )
    except Exception as exc:
        _dbg("Failed to initialize log file: {}".format(exc))
        _log_path = None
    return _log_path


def _append_log_row(payload):
    if _log_path is None:
        return
    try:
        with open(_log_path, "a") as fh:
            fh.write(
                "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n".format(
                    _csv_time_stamp(),
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
    except Exception as exc:
        _dbg("Log append failed: {}".format(exc))


def _load_canopy_state():
    default_open = bool(getattr(config, "SERVO_DEFAULT_OPEN", False))
    path = str(getattr(config, "SERVO_STATE_FILE", "canopy_state.txt"))
    try:
        with open(path, "r") as fh:
            raw = fh.read().strip().lower()
        if raw in ("open", "1", "true"):
            return True
        if raw in ("closed", "0", "false"):
            return False
    except Exception:
        pass
    return default_open


def _save_canopy_state(is_open):
    path = str(getattr(config, "SERVO_STATE_FILE", "canopy_state.txt"))
    tmp_path = path + ".tmp"
    text = "open\n" if is_open else "closed\n"
    try:
        with open(tmp_path, "w") as fh:
            fh.write(text)
        try:
            os.remove(path)
        except Exception:
            pass
        os.rename(tmp_path, path)
        return True
    except Exception as exc:
        _dbg("Failed to persist canopy state: {}".format(exc))
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


def _parse_hm_to_minutes(text):
    try:
        value = str(text).strip()
        parts = value.split(":")
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return (hour * 60) + minute
    except Exception:
        return None


def _is_canopy_scheduled_open_now():
    open_min = _parse_hm_to_minutes(getattr(config, "CANOPY_OPEN_TIME_HM", "07:00"))
    close_min = _parse_hm_to_minutes(getattr(config, "CANOPY_CLOSE_TIME_HM", "20:00"))
    if open_min is None or close_min is None:
        return None

    now = time.localtime()
    now_min = (int(now[3]) * 60) + int(now[4])

    if open_min == close_min:
        return False
    if open_min < close_min:
        return open_min <= now_min < close_min
    return (now_min >= open_min) or (now_min < close_min)


def _effective_rain_pct(rain_now_pct, rain_3h_pct):
    vals = []
    if isinstance(rain_now_pct, (int, float)):
        vals.append(float(rain_now_pct))
    if isinstance(rain_3h_pct, (int, float)):
        vals.append(float(rain_3h_pct))
    if not vals:
        return None
    return max(vals)


def _apply_canopy_rules(rain_now_pct, rain_3h_pct):
    desired_open = _canopy_open

    if bool(getattr(config, "CANOPY_SCHEDULE_ENABLED", False)):
        scheduled_open = _is_canopy_scheduled_open_now()
        if scheduled_open is not None:
            desired_open = scheduled_open

    if bool(getattr(config, "CANOPY_RAIN_OVERRIDE_ENABLED", False)):
        rain_threshold = int(getattr(config, "CANOPY_RAIN_CLOSE_PCT", 70))
        if rain_threshold < 0:
            rain_threshold = 0
        elif rain_threshold > 100:
            rain_threshold = 100

        effective_rain = _effective_rain_pct(rain_now_pct, rain_3h_pct)
        if effective_rain is not None and effective_rain >= rain_threshold:
            desired_open = False

    return desired_open


def _url_decode(text):
    out = []
    i = 0
    text = text.replace("+", " ")
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "%" and i + 2 < n:
            try:
                out.append(chr(int(text[i + 1:i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def _html_escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _is_config_symbol_key(key):
    if not key:
        return False
    # MicroPython builds may omit str.isalnum(); keep key checks ASCII-only.
    for ch in key:
        if ch == "_":
            continue
        code = ord(ch)
        if 48 <= code <= 57:
            continue
        if 65 <= code <= 90:
            continue
        return False
    return True


def _is_web_editable_config_key(key):
    # Keep hardware pin wiring and EPD tuning out of the web editor.
    if key.startswith("EPD_"):
        return False
    if "PIN" in key:
        return False
    return True


def _extract_config_keys(lines):
    keys = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        left, _right = stripped.split("=", 1)
        key = left.strip()
        if key and key.upper() == key and _is_config_symbol_key(key):
            keys.add(key)
    return keys


def _missing_critical_config_keys(lines):
    found = _extract_config_keys(lines)
    missing = []
    for key in _CRITICAL_CONFIG_KEYS:
        if key not in found:
            missing.append(key)
    return missing


def _recover_config_from_backup_if_needed():
    path = str(getattr(config, "CONFIG_FILE_PATH", "config.py"))
    bak_path = path + ".bak"
    tmp_path = path + ".restore_tmp"
    bad_path = path + ".corrupt"

    current_lines = []
    try:
        with open(path, "r") as fh:
            current_lines = fh.readlines()
    except Exception:
        current_lines = []

    missing = _missing_critical_config_keys(current_lines)
    if not missing:
        return False

    backup_lines = []
    try:
        with open(bak_path, "r") as fh:
            backup_lines = fh.readlines()
    except Exception:
        _dbg("Config missing critical keys and no readable backup: {}".format(", ".join(missing)))
        return False

    backup_missing = _missing_critical_config_keys(backup_lines)
    if backup_missing:
        _dbg("Config backup also missing critical keys: {}".format(", ".join(backup_missing)))
        return False

    try:
        with open(tmp_path, "w") as fh:
            fh.write("".join(backup_lines))

        try:
            os.remove(bad_path)
        except Exception:
            pass

        try:
            os.rename(path, bad_path)
        except Exception:
            pass

        os.rename(tmp_path, path)
        _dbg("Recovered config from backup; missing keys were: {}".format(", ".join(missing)))
        return True
    except Exception as exc:
        _dbg("Config recovery failed: {}".format(exc))
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


def _ensure_config_defaults_loaded():
    missing = []
    for key, default in _REQUIRED_CONFIG_DEFAULTS.items():
        if not hasattr(config, key):
            setattr(config, key, default)
            missing.append(key)
    if missing:
        print("[WARN] Missing config keys at runtime; using defaults: {}".format(", ".join(missing)))


def _load_config_vars():
    path = str(getattr(config, "CONFIG_FILE_PATH", "config.py"))
    lines = []
    vars_map = {}
    try:
        with open(path, "r") as fh:
            lines = fh.readlines()
    except Exception:
        return path, lines, vars_map

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        left, right = stripped.split("=", 1)
        key = left.strip()
        if key and key.upper() == key and _is_config_symbol_key(key) and _is_web_editable_config_key(key):
            vars_map[key] = right.strip()

    # Show newer web-editable defaults even if config.py is from an older build.
    for key in _REQUIRED_CONFIG_DEFAULTS:
        if _is_web_editable_config_key(key) and key not in vars_map:
            vars_map[key] = repr(getattr(config, key))

    for key in (
        "CANOPY_SCHEDULE_ENABLED",
        "CANOPY_OPEN_TIME_HM",
        "CANOPY_CLOSE_TIME_HM",
        "CANOPY_RAIN_OVERRIDE_ENABLED",
        "CANOPY_RAIN_CLOSE_PCT",
    ):
        if key not in vars_map and hasattr(config, key):
            vars_map[key] = repr(getattr(config, key))

    return path, lines, vars_map


def _coerce_config_literal(new_value, existing_literal):
    text = str(new_value)
    stripped = text.strip()
    existing = str(existing_literal).strip()

    # Preserve bool literal style.
    if existing in ("True", "False"):
        low = stripped.lower()
        if low == "true":
            return "True"
        if low == "false":
            return "False"

    # If existing value is a quoted string, keep it as a quoted string.
    if len(existing) >= 2 and existing[0] == existing[-1] and existing[0] in ('"', "'"):
        quote = existing[0]
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'"):
            return stripped
        escaped = stripped.replace("\\", "\\\\")
        if quote == '"':
            escaped = escaped.replace('"', '\\"')
        else:
            escaped = escaped.replace("'", "\\'")
        return quote + escaped + quote

    return stripped


def _save_config_vars(updated_values):
    path, lines, _ = _load_config_vars()
    if not lines:
        return False, 0

    changed = False
    change_count = 0
    found_keys = set()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        left, _right = stripped.split("=", 1)
        key = left.strip()
        found_keys.add(key)
        if key in updated_values:
            new_line = "{} = {}\n".format(key, updated_values[key])
            if lines[idx] != new_line:
                lines[idx] = new_line
                changed = True
                change_count += 1

    # Append keys that did not exist in older config files.
    for key in updated_values:
        if key not in found_keys:
            lines.append("{} = {}\n".format(key, updated_values[key]))
            changed = True
            change_count += 1

    if not changed:
        return False, 0

    missing = _missing_critical_config_keys(lines)
    if missing:
        _dbg("Config save aborted; missing critical keys: {}".format(", ".join(missing)))
        return False, 0

    tmp_path = path + ".tmp"
    bak_path = path + ".bak"

    try:
        with open(tmp_path, "w") as fh:
            fh.write("".join(lines))

        # Atomic-ish replace: keep a backup then swap temp into place.
        try:
            os.remove(bak_path)
        except Exception:
            pass

        try:
            os.rename(path, bak_path)
        except Exception:
            # If original is missing, continue and try to place temp anyway.
            pass

        try:
            os.rename(tmp_path, path)
        except Exception as exc:
            _dbg("Config swap failed: {}".format(exc))
            try:
                os.rename(bak_path, path)
            except Exception:
                pass
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return False, 0

        return True, change_count
    except Exception as exc:
        _dbg("Config save failed: {}".format(exc))
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False, 0


def _wifi_is_connected():
    if network is None:
        return False
    try:
        wlan = network.WLAN(network.STA_IF)
        return bool(wlan.active() and wlan.isconnected())
    except Exception:
        return False


def _read_button(pin):
    try:
        return pin.value() == 0
    except Exception:
        return False


def _smoothstep(t):
    return t * t * (3.0 - (2.0 * t))


def _servo_move_params():
    # Keep servo drive in a torque-friendly region.
    total_ms = int(getattr(config, "SERVO_MOVE_TOTAL_MS", 8_000))
    step_ms = int(getattr(config, "SERVO_MOVE_STEP_MS", 35))
    ramp = float(getattr(config, "SERVO_RAMP_STRENGTH", 0.12))
    min_step_deg = float(getattr(config, "SERVO_MIN_STEP_DEG", 1.5))
    breakaway_deg = float(getattr(config, "SERVO_BREAKAWAY_DEG", 6.0))
    breakaway_hold_ms = int(getattr(config, "SERVO_BREAKAWAY_HOLD_MS", 120))

    if total_ms < 2_000:
        total_ms = 2_000
    if step_ms < 20:
        step_ms = 20
    elif step_ms > 60:
        step_ms = 60

    # Too-strong easing can reduce endpoint drive increments.
    if ramp < 0.0:
        ramp = 0.0
    elif ramp > 0.35:
        ramp = 0.35

    if min_step_deg < 0.5:
        min_step_deg = 0.5
    elif min_step_deg > 6.0:
        min_step_deg = 6.0

    if breakaway_deg < 0.0:
        breakaway_deg = 0.0
    elif breakaway_deg > 15.0:
        breakaway_deg = 15.0

    if breakaway_hold_ms < 0:
        breakaway_hold_ms = 0
    elif breakaway_hold_ms > 500:
        breakaway_hold_ms = 500

    return total_ms, step_ms, ramp, min_step_deg, breakaway_deg, breakaway_hold_ms


def _set_servo_rail(enabled):
    global _servo_rail_pin, _servo_rail_init_failed

    if _servo_rail_pin is None and (not _servo_rail_init_failed):
        pin_id = int(getattr(config, "SERVO_RAIL_ENABLE_PIN", 2))
        try:
            _servo_rail_pin = machine.Pin(pin_id, machine.Pin.OUT)
        except Exception as exc:
            _dbg("Servo rail pin init failed: {}".format(exc))
            _servo_rail_init_failed = True

    if _servo_rail_pin is None:
        return False

    active_high = bool(getattr(config, "SERVO_RAIL_ACTIVE_HIGH", True))
    level = 1 if (enabled == active_high) else 0
    try:
        _servo_rail_pin.value(level)
        return True
    except Exception as exc:
        _dbg("Servo rail set failed: {}".format(exc))
        return False


def _move_servo_with_power(servo, target, motion_mode):
    settle_ms = int(getattr(config, "SERVO_RAIL_SETTLE_MS", 80))
    off_delay_ms = int(getattr(config, "SERVO_RAIL_OFF_DELAY_MS", 80))
    if settle_ms < 0:
        settle_ms = 0
    if off_delay_ms < 0:
        off_delay_ms = 0

    _set_servo_rail(True)
    if settle_ms:
        time.sleep_ms(settle_ms)

    try:
        (
            move_total_ms,
            move_step_ms,
            move_ramp,
            move_min_step_deg,
            move_breakaway_deg,
            move_breakaway_hold_ms,
        ) = _servo_move_params()

        if motion_mode == "simple":
            _dbg("Servo simple move target={}".format(target))
            servo.set_angle(target)
        else:
            _dbg(
                "Servo move start target={} total={}ms step={}ms ramp={} min_step={} breakaway={} hold={}ms".format(
                    target,
                    move_total_ms,
                    move_step_ms,
                    move_ramp,
                    move_min_step_deg,
                    move_breakaway_deg,
                    move_breakaway_hold_ms,
                )
            )
            servo.move_angle(
                target,
                total_ms=move_total_ms,
                step_ms=move_step_ms,
                ramp_strength=move_ramp,
                min_step_deg=move_min_step_deg,
                breakaway_deg=move_breakaway_deg,
                breakaway_hold_ms=move_breakaway_hold_ms,
            )
    finally:
        if off_delay_ms:
            time.sleep_ms(off_delay_ms)
        _set_servo_rail(False)


def _set_canopy_state(servo, should_open, force=False, motion_mode=None):
    global _canopy_open

    if (not force) and (_canopy_open == bool(should_open)):
        return False

    if motion_mode is None:
        motion_mode = str(getattr(config, "SERVO_MOTION_MODE", "ramped")).strip().lower()

    target = getattr(config, "SERVO_OPEN_ANGLE", 90) if should_open else getattr(config, "SERVO_CLOSED_ANGLE", 0)
    _move_servo_with_power(servo, target, motion_mode)
    _canopy_open = bool(should_open)
    _save_canopy_state(_canopy_open)
    return True


def _safe_epd_draw(epd, draw_func, *args):
    global _epd_cached, _epd_failed_once

    if epd is None:
        return None
    try:
        draw_func(epd, *args)
        return epd
    except OSError as exc:
        if "busy timeout" not in str(exc).lower():
            raise
        _dbg("EPD busy timeout during draw; attempting reinit")
        try:
            epd.init()
            draw_func(epd, *args)
            return epd
        except Exception as rec_exc:
            _dbg("EPD recovery failed: {}".format(rec_exc))
            _epd_cached = None
            _epd_failed_once = True
            return None


def _resolve_refresh_mode(requested_mode):
    if requested_mode == "quick" and not bool(getattr(config, "EPD_ENABLE_QUICK_REFRESH", False)):
        return "full"
    return requested_mode


def _sleep_ms_with_poll(ms, pin_mode=None, pin_servo=None):
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        time.sleep_ms(20)
        # If either button stays pressed, just keep draining until released.
        if pin_mode is not None and _read_button(pin_mode):
            continue
        if pin_servo is not None and _read_button(pin_servo):
            continue


def _wifi_connect(timeout_ms=None):
    if network is None:
        _dbg("network module unavailable")
        return None

    ssid = getattr(config, "WIFI_SSID", "")
    password = getattr(config, "WIFI_PASSWORD", "")

    if not ssid:
        _dbg("WIFI_SSID not set; skipping weather")
        return None

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    _dbg("Wi-Fi connect start: {}".format(ssid))
    _web_log("Wi-Fi connect start: {}".format(ssid))
    try:
        wlan.connect(ssid, password)
    except Exception as exc:
        _dbg("Wi-Fi connect call failed: {}".format(exc))
        _web_log("Wi-Fi connect call failed: {}".format(exc))
        return None

    if timeout_ms is None:
        timeout_ms = int(getattr(config, "WIFI_CONNECT_TIMEOUT_MS", 30_000))
    start = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            _dbg("Wi-Fi connect timeout")
            try:
                st = wlan.status()
            except Exception:
                st = "?"
            _web_log("Wi-Fi connect timeout; status={}".format(st))
            return None
        time.sleep_ms(200)

    try:
        ip = wlan.ifconfig()[0]
    except Exception:
        ip = "?"
    _dbg("Wi-Fi connected, IP={}".format(ip))
    _web_log("Wi-Fi connected, IP={}".format(ip))
    return wlan


def _wifi_start_ap():
    if network is None:
        return None
    try:
        ap = network.WLAN(network.AP_IF)
    except Exception:
        return None

    ssid = str(getattr(config, "WIFI_AP_SSID", "BiomeLX-Setup"))
    password = str(getattr(config, "WIFI_AP_PASSWORD", ""))
    try:
        if password:
            try:
                ap.config(essid=ssid, password=password, authmode=3)
            except Exception:
                ap.config(essid=ssid, password=password)
        else:
            ap.config(essid=ssid)
    except Exception as exc:
        _dbg("AP config failed: {}".format(exc))

    try:
        ap.active(True)
        _dbg("AP active: {}".format(ssid))
        return ap
    except Exception as exc:
        _dbg("AP start failed: {}".format(exc))
        return None


def _wifi_disable_all():
    if network is None:
        return
    try:
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            try:
                sta.disconnect()
            except Exception:
                pass
            sta.active(False)
    except Exception:
        pass
    try:
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            ap.active(False)
    except Exception:
        pass


def _sync_time_from_web():
    if not bool(getattr(config, "ENABLE_WEB_TIME_SYNC", True)):
        return False
    if not _wifi_is_connected():
        return False

    def _set_local_time_from_epoch(epoch_utc):
        try:
            offset_hours = int(getattr(config, "TIMEZONE_OFFSET_HOURS", 0))
            lt = time.localtime(int(epoch_utc) + (offset_hours * 3600))
            rtc = machine.RTC()
            rtc.datetime((lt[0], lt[1], lt[2], lt[6], lt[3], lt[4], lt[5], 0))
            return True
        except Exception as exc:
            _dbg("RTC set failed: {}".format(exc))
            return False

    def _sync_time_from_http_fallback():
        if requests is None:
            return False
        url = str(getattr(config, "TIME_HTTP_FALLBACK_URL", "http://worldtimeapi.org/api/timezone/Etc/UTC"))
        response = None
        try:
            response = requests.get(url)
            status_code = getattr(response, "status_code", 200)
            if status_code != 200:
                _dbg("HTTP time status {}".format(status_code))
                return False
            data = response.json()
            if not isinstance(data, dict):
                return False
            epoch_utc = data.get("unixtime")
            if not isinstance(epoch_utc, (int, float)):
                _dbg("HTTP time missing unixtime")
                return False
            if _set_local_time_from_epoch(epoch_utc):
                _dbg("Time synchronized from HTTP fallback")
                return True
            return False
        except Exception as exc:
            _dbg("HTTP time sync failed: {}".format(exc))
            return False
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    min_valid_year = int(getattr(config, "TIME_SYNC_MIN_VALID_YEAR", 2024))
    retries = int(getattr(config, "TIME_SYNC_NTP_RETRIES", 3))
    if retries < 1:
        retries = 1
    elif retries > 5:
        retries = 5

    if ntptime is not None:
        try:
            ntptime.host = str(getattr(config, "NTP_HOST", "pool.ntp.org"))
        except Exception:
            pass

        for attempt in range(retries):
            try:
                ntptime.settime()
                if _set_local_time_from_epoch(time.time()):
                    if time.localtime()[0] >= min_valid_year:
                        _dbg("Time synchronized from NTP")
                        return True
            except Exception as exc:
                _dbg("NTP sync attempt {}/{} failed: {}".format(attempt + 1, retries, exc))
            time.sleep_ms(150)
    else:
        _dbg("ntptime unavailable")

    if bool(getattr(config, "TIME_HTTP_FALLBACK_ENABLED", True)):
        if _sync_time_from_http_fallback() and time.localtime()[0] >= min_valid_year:
            return True

    _dbg("Time sync failed; keeping local RTC")
    return False


def _http_send(client, status, content_type, body, extra_headers=None):
    if isinstance(body, str):
        body = body.encode("utf-8")
    if extra_headers is None:
        extra_headers = []

    headers = [
        "HTTP/1.1 {}\r\n".format(status),
        "Content-Type: {}\r\n".format(content_type),
        "Content-Length: {}\r\n".format(len(body)),
        "Connection: close\r\n",
    ]
    for line in extra_headers:
        headers.append("{}\r\n".format(line))
    headers.append("\r\n")
    _socket_send_all(client, "".join(headers).encode("utf-8"))
    if body:
        _socket_send_all(client, body)


def _socket_send_all(client, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    sent = 0
    total = len(data)
    while sent < total:
        n = client.send(data[sent:])
        if not n:
            break
        sent += n


def _http_send_start(client, status, content_type):
    _socket_send_all(
        client,
        (
            "HTTP/1.1 {}\r\n"
            "Content-Type: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(status, content_type),
    )


def _render_web_index(message=""):
    _path, _lines, vars_map = _load_config_vars()
    keys = sorted(vars_map.keys())

    sections = {
        "Network": [],
        "Weather": [],
        "Time": [],
        "Logging": [],
        "Power + Wake": [],
        "Sensors + Sampling": [],
        "Servo": [],
        "System": [],
    }

    def _section_for_key(key):
        if key.startswith("WIFI_") or key.startswith("WEB_UI_"):
            return "Network"
        if key.startswith("WEATHER_") or key == "ENABLE_WEATHER":
            return "Weather"
        if key in ("ENABLE_WEB_TIME_SYNC", "NTP_HOST", "TIMEZONE_OFFSET_HOURS"):
            return "Time"
        if key.startswith("LOG_"):
            return "Logging"
        if "WAKE" in key or "SLEEP" in key or key.startswith("BUTTON_") or key in ("ENABLE_LOW_POWER_SLEEP", "ENABLE_BUTTON_WAKE"):
            return "Power + Wake"
        if key.startswith("BME_") or key.startswith("INA_") or key.startswith("I2C_") or key.startswith("SENSOR_"):
            return "Sensors + Sampling"
        if key.startswith("CANOPY_"):
            return "Servo"
        if key.startswith("SERVO_"):
            return "Servo"
        return "System"

    for key in keys:
        sections[_section_for_key(key)].append(key)

    section_html = []
    for name in ("Network", "Weather", "Time", "Logging", "Power + Wake", "Sensors + Sampling", "Servo", "System"):
        sec_keys = sections[name]
        if not sec_keys:
            continue
        rows = []
        for key in sorted(sec_keys):
            rows.append(
                "<div class=\"row\">"
                "<label for=\"{}\">{}</label>"
                "<input id=\"{}\" name=\"{}\" value=\"{}\"/>"
                "</div>".format(
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(key),
                    _html_escape(vars_map[key]),
                )
            )
        section_html.append(
            "<section><h2>{}</h2><div class=\"rows\">{}</div></section>".format(
                _html_escape(name),
                "".join(rows),
            )
        )

    info = ""
    if _log_path:
        info = "<div class=\"info\">Active log: {}</div>".format(_html_escape(_log_path))

    msg_html = ""
    if message:
        msg_html = "<div class=\"msg\">{}</div>".format(_html_escape(message))

    return (
        "<html><head><title>BiomeLX</title>"
        "<style>"
        "body{{margin:0;padding:20px;background:#f5f7f8;color:#1f2933;font:14px/1.4 'Segoe UI',Tahoma,sans-serif;}}"
        ".wrap{{max-width:860px;margin:0 auto;background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:20px 22px;}}"
        "h1{{margin:0 0 12px;font-size:24px;}}"
        "h2{{margin:0 0 10px;font-size:16px;color:#334e68;}}"
        "section{{padding:14px 0;border-top:1px solid #e4e7eb;}}"
        "section:first-of-type{{border-top:0;padding-top:4px;}}"
        ".rows{{display:block;}}"
        ".row{{display:flex;align-items:center;gap:14px;margin:8px 0;}}"
        "label{{width:290px;min-width:290px;color:#243b53;font-weight:600;}}"
        "input{{flex:1;min-width:0;padding:8px 10px;border:1px solid #bcccdc;border-radius:6px;background:#fff;}}"
        "input:focus{{outline:none;border-color:#486581;box-shadow:0 0 0 2px #d9e2ec;}}"
        ".msg{{margin:10px 0 12px;padding:10px 12px;border:1px solid #bcd7f7;background:#eff6ff;border-radius:6px;color:#1e429f;}}"
        ".info{{margin:10px 0 14px;padding:10px 12px;border:1px solid #d9e2ec;background:#f8fbfd;border-radius:6px;color:#486581;}}"
        ".actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;padding-top:14px;border-top:1px solid #e4e7eb;}}"
        "button,a.btn{{padding:9px 14px;border-radius:6px;border:1px solid #829ab1;background:#334e68;color:#fff;text-decoration:none;cursor:pointer;font-weight:600;}}"
        "button.secondary,a.btn.secondary{{background:#fff;color:#334e68;}}"
        "button.danger{{background:#8b1e3f;border-color:#8b1e3f;}}"
        "@media (max-width:760px){{label{{width:100%;min-width:0}}.row{{display:block}}.row input{{width:100%;box-sizing:border-box;margin-top:6px}}}}"
        "</style></head><body><div class=\"wrap\">"
        "<h1>BiomeLX Web UI</h1>"
        "{}{}"
        "<form method=\"POST\" action=\"/save\">{}"
        "<div class=\"actions\">"
        "<button type=\"submit\">Save Config</button>"
        "<a class=\"btn secondary\" href=\"/logs\">Download Logs</a>"
        "</div></form>"
        "<form method=\"POST\" action=\"/reset\" style=\"margin-top:10px\">"
        "<button class=\"danger\" type=\"submit\">Hard Reset</button>"
        "</form>"
        "</div></body></html>"
    ).format(msg_html, info, "".join(section_html))


def _send_web_index(client, message=""):
    # Stream HTML in small chunks to avoid large heap allocations.
    _path, _lines, vars_map = _load_config_vars()
    keys = sorted(vars_map.keys())

    sections = {
        "Network": [],
        "Weather": [],
        "Time": [],
        "Logging": [],
        "Power + Wake": [],
        "Sensors + Sampling": [],
        "Servo": [],
        "System": [],
    }

    def _section_for_key(key):
        if key.startswith("WIFI_") or key.startswith("WEB_UI_"):
            return "Network"
        if key.startswith("WEATHER_") or key == "ENABLE_WEATHER":
            return "Weather"
        if key in ("ENABLE_WEB_TIME_SYNC", "NTP_HOST", "TIMEZONE_OFFSET_HOURS"):
            return "Time"
        if key.startswith("LOG_"):
            return "Logging"
        if "WAKE" in key or "SLEEP" in key or key.startswith("BUTTON_") or key in ("ENABLE_LOW_POWER_SLEEP", "ENABLE_BUTTON_WAKE"):
            return "Power + Wake"
        if key.startswith("BME_") or key.startswith("INA_") or key.startswith("I2C_") or key.startswith("SENSOR_"):
            return "Sensors + Sampling"
        if key.startswith("SERVO_"):
            return "Servo"
        return "System"

    for key in keys:
        sections[_section_for_key(key)].append(key)

    _http_send_start(client, "200 OK", "text/html; charset=utf-8")
    _socket_send_all(
        client,
        "<html><head><title>BiomeLX</title>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<style>"
        "body{font:14px sans-serif;margin:10px;background:#f7f7f7}"
        ".card{background:#fff;border:1px solid #ccc;padding:10px}"
        "fieldset{margin:10px 0;border:1px solid #ccc}legend{font-weight:bold}"
        ".row{display:flex;gap:8px;align-items:center;margin:6px 0}"
        ".row label{width:46%;font-size:12px}"
        ".row input{width:54%;padding:4px}"
        ".actions{margin-top:10px}"
        "button,a{padding:7px 10px;margin-right:8px}"
        "@media(max-width:760px){.row{display:block}.row label,.row input{width:100%}}"
        "</style></head><body><div class=\"card\">"
        "<h1>BiomeLX Web UI</h1>",
    )

    if message:
        _socket_send_all(client, "<p><b>{}</b></p>".format(_html_escape(message)))
    if _log_path:
        _socket_send_all(client, "<p>Active log: {}</p>".format(_html_escape(_log_path)))

    _socket_send_all(client, "<form method=\"POST\" action=\"/save\">")
    for name in ("Network", "Weather", "Time", "Logging", "Power + Wake", "Sensors + Sampling", "Servo", "System"):
        sec_keys = sections[name]
        if not sec_keys:
            continue
        _socket_send_all(client, "<fieldset><legend>{}</legend>".format(_html_escape(name)))
        for key in sorted(sec_keys):
            _socket_send_all(
                client,
                "<div class=\"row\"><label for=\"{k}\">{k}</label>"
                "<input id=\"{k}\" name=\"{k}\" value=\"{v}\"></div>".format(
                    k=_html_escape(key),
                    v=_html_escape(vars_map[key]),
                ),
            )
        _socket_send_all(client, "</fieldset>")

    _socket_send_all(
        client,
        "<div class=\"actions\">"
        "<button type=\"submit\">Save Config</button>"
        "<a href=\"/logs\">Download Logs</a>"
        "</div></form>"
        "<form method=\"POST\" action=\"/reset\" style=\"margin-top:10px\">"
        "<button type=\"submit\">Hard Reset</button>"
        "</form></div></body></html>",
    )


def _render_logs_page():
    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    links = []
    try:
        names = sorted(os.listdir(log_dir))
    except Exception:
        names = []

    for name in names:
        if name.endswith(".csv"):
            links.append('<li><a href="/logs/{}">{}</a></li>'.format(_html_escape(name), _html_escape(name)))

    return (
        "<html><head><title>BiomeLX Logs</title></head><body>"
        "<h1>Logs</h1><ul>{}</ul>"
        "<p><a href=\"/\">Back</a></p>"
        "</body></html>"
    ).format("".join(links))


def _parse_form(body):
    out = {}
    parts = body.split("&")
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        key = _url_decode(k)
        value = _url_decode(v)
        if key:
            out[key] = value
    return out


def _serve_log_file(client, name):
    if not name or "/" in name or ".." in name or "\\" in name:
        _http_send(client, "400 Bad Request", "text/plain", "Invalid filename")
        return

    log_dir = str(getattr(config, "LOG_DIR", "logs"))
    path = "{}/{}".format(log_dir, name)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        _http_send(
            client,
            "200 OK",
            "text/csv",
            data,
            extra_headers=["Content-Disposition: attachment; filename={}".format(name)],
        )
    except Exception:
        _http_send(client, "404 Not Found", "text/plain", "Log not found")


def _handle_http_request(client, method, path, body):
    clean_path = path.split("?", 1)[0]
    if method == "GET" and clean_path == "/":
        _send_web_index(client)
        return

    if method == "GET" and clean_path == "/logs":
        _http_send(client, "200 OK", "text/html", _render_logs_page())
        return

    if method == "GET" and clean_path.startswith("/logs/"):
        _serve_log_file(client, clean_path[len("/logs/"):])
        return

    if method == "POST" and clean_path == "/save":
        updates = _parse_form(body)
        _path, _lines, existing = _load_config_vars()
        to_save = {}
        for key in existing:
            if key in updates:
                value = updates[key]
                # Keep current credentials when an empty value is submitted.
                if key in ("WIFI_SSID", "WIFI_PASSWORD") and not str(value).strip():
                    continue
                coerced = _coerce_config_literal(value, existing[key])
                if coerced != str(existing[key]).strip():
                    to_save[key] = coerced
        saved, count = _save_config_vars(to_save) if to_save else (False, 0)
        _web_log("Config save requested: posted={} staged={} changed={}".format(len(updates), len(to_save), count))

        # Keep save response tiny to avoid heap pressure from full page re-render.
        if saved:
            body_html = (
                "<html><body><h1>Saved {}</h1>"
                "<p>Reboot to apply.</p>"
                "<p><a href=\"/\">Back to config</a></p>"
                "</body></html>"
            ).format(count)
        else:
            body_html = (
                "<html><body><h1>No changes saved</h1>"
                "<p><a href=\"/\">Back to config</a></p>"
                "</body></html>"
            )
        _http_send(client, "303 See Other", "text/html", body_html, extra_headers=["Location: /"])
        return

    if method == "POST" and clean_path == "/reset":
        _http_send(client, "200 OK", "text/html", "<html><body><h1>Rebooting...</h1></body></html>")
        time.sleep_ms(150)
        machine.reset()
        return

    _http_send(client, "404 Not Found", "text/plain", "Not found")


def _start_web_server():
    global _web_server, _web_server_port

    if socket is None:
        _dbg("socket module unavailable; web UI disabled")
        _web_log("socket module unavailable")
        _web_server = None
        _web_server_port = None
        return None

    _stop_web_server()

    base_port = int(getattr(config, "WEB_UI_PORT", 80))
    alt_port = int(getattr(config, "WEB_UI_ALT_PORT", 8080))
    port_candidates = [base_port]
    if alt_port not in port_candidates:
        port_candidates.append(alt_port)

    _web_server = None
    _web_server_port = None
    for port in port_candidates:
        server = None
        try:
            server = socket.socket()
            try:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass

            bind_addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
            server.bind(bind_addr)
            server.listen(2)
            server.settimeout(0.05)
            _web_server = server
            _web_server_port = port
            _dbg("Web UI on :{}".format(port))
            _web_log("Web UI listening on 0.0.0.0:{}".format(port))
            break
        except Exception as exc:
            _web_log("Web UI bind failed on {}: {}".format(port, exc))
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass

    if _web_server is None:
        _dbg("Web UI start failed on all ports")
        _web_log("Web UI unavailable")
    return _web_server


def _stop_web_server():
    global _web_server, _web_server_port
    if _web_server is not None:
        try:
            _web_server.close()
        except Exception:
            pass
    _web_server_port = None
    _web_server = None


def _poll_web_server_once():
    if _web_server is None:
        return

    client = None
    client_addr = None
    try:
        client, client_addr = _web_server.accept()
    except OSError:
        return
    except Exception:
        return

    try:
        client_timeout_s = float(getattr(config, "WEB_UI_CLIENT_TIMEOUT_S", 1.5))
        if client_timeout_s < 0.1:
            client_timeout_s = 0.1
        client.settimeout(client_timeout_s)
        req = b""
        while b"\r\n\r\n" not in req and len(req) < 4096:
            try:
                chunk = client.recv(512)
            except OSError as exc:
                if _is_socket_timeout(exc):
                    if req:
                        break
                    _web_log("Client {} timed out before request headers".format(client_addr))
                    return
                raise
            if not chunk:
                break
            req += chunk

        if not req:
            return

        header_blob, body_blob = (req.split(b"\r\n\r\n", 1) + [b""])[:2]
        header_text = header_blob.decode("utf-8", "ignore")
        lines = header_text.split("\r\n")
        request_line = lines[0] if lines else ""
        parts = request_line.split(" ")
        if len(parts) < 2:
            _http_send(client, "400 Bad Request", "text/plain", "Malformed request")
            return

        method = parts[0].upper()
        path = parts[1]
        _web_log("{} {} from {}".format(method, path, client_addr))

        content_length = 0
        for line in lines[1:]:
            low = line.lower()
            if low.startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except Exception:
                    content_length = 0
                break

        while len(body_blob) < content_length:
            try:
                chunk = client.recv(min(512, content_length - len(body_blob)))
            except OSError as exc:
                if _is_socket_timeout(exc):
                    _web_log("Client {} body read timeout".format(client_addr))
                    break
                raise
            if not chunk:
                break
            body_blob += chunk

        body = body_blob.decode("utf-8", "ignore") if body_blob else ""
        _handle_http_request(client, method, path, body)
    except Exception as exc:
        if _is_socket_timeout(exc):
            _web_log("Client {} timed out".format(client_addr))
            return
        _dbg("Web request failed: {}".format(exc))
        _web_log("Web request failed: {}".format(exc))
        try:
            _http_send(client, "500 Internal Server Error", "text/plain", "Internal error")
        except Exception:
            pass
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _is_rain_code(weather_code):
    if weather_code is None:
        return False
    code = int(weather_code)
    return (
        (51 <= code <= 67)
        or (80 <= code <= 82)
        or (95 <= code <= 99)
    )


def _read_weather():
    if not getattr(config, "ENABLE_WEATHER", True):
        _dbg("weather disabled in config")
        return None

    if requests is None:
        _dbg("requests module unavailable")
        return None

    lat = getattr(config, "WEATHER_LATITUDE", None)
    lon = getattr(config, "WEATHER_LONGITUDE", None)
    if lat is None or lon is None:
        _dbg("WEATHER_LATITUDE/WEATHER_LONGITUDE not set")
        return None

    connect_retries = int(getattr(config, "WIFI_PULL_CONNECT_RETRIES", 2))
    if connect_retries < 1:
        connect_retries = 1
    if connect_retries > 4:
        connect_retries = 4

    if (not _wireless_mode) and bool(getattr(config, "WIFI_PULL_RESET_RADIO", True)):
        _wifi_disable_all()
        time.sleep_ms(200)

    wlan = None
    for attempt in range(connect_retries):
        wlan = _wifi_connect()
        if wlan is not None:
            break
        _dbg("weather Wi-Fi connect retry {}/{}".format(attempt + 1, connect_retries))
        time.sleep_ms(300)

    if wlan is None:
        _dbg("weather skipped: Wi-Fi unavailable")
        return None
    _sync_time_from_web()

    rain_soon_hours = int(getattr(config, "WEATHER_RAIN_SOON_HOURS", 3))
    if rain_soon_hours < 1:
        rain_soon_hours = 1
    if rain_soon_hours > 6:
        rain_soon_hours = 6

    rain_3h_hours = 3

    base_url = str(getattr(config, "WEATHER_API_BASE_URL", "https://api.open-meteo.com/v1/forecast"))
    url = (
        "{base}"
        "?latitude={lat}&longitude={lon}"
        "&current=cloud_cover,precipitation,rain,weather_code"
        "&hourly=precipitation_probability"
        "&forecast_hours={hours}"
        "&timezone=auto"
    ).format(base=base_url, lat=lat, lon=lon, hours=max(rain_soon_hours, rain_3h_hours))

    response = None
    try:
        try_urls = [url]
        if bool(getattr(config, "WEATHER_ALLOW_HTTP_FALLBACK", True)) and url.startswith("https://"):
            try_urls.append("http://" + url[len("https://"):])

        response = None
        last_error = None
        for try_url in try_urls:
            try:
                response = requests.get(try_url)
                if try_url != url:
                    _dbg("weather fetched via HTTP fallback")
                break
            except Exception as exc:
                last_error = exc
                response = None

        if response is None:
            if last_error is not None:
                raise last_error
            return None

        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            _dbg("weather HTTP status {}".format(status_code))
            return None

        try:
            data = response.json()
        except Exception:
            data = None
        if not isinstance(data, dict):
            _dbg("weather response parse failed")
            return None

        current = data.get("current", {})
        cloud_cover = current.get("cloud_cover")
        rain_now_mm = current.get("rain")
        precipitation_now = current.get("precipitation")
        weather_code = current.get("weather_code")

        sun_pct = None
        if isinstance(cloud_cover, (int, float)):
            sun_pct = max(0, min(100, int(100 - cloud_cover)))

        raining_now = False
        if isinstance(rain_now_mm, (int, float)) and rain_now_mm > 0:
            raining_now = True
        if isinstance(precipitation_now, (int, float)) and precipitation_now > 0:
            raining_now = True
        if _is_rain_code(weather_code):
            raining_now = True

        rain_now_pct = None
        rain_3h_pct = None
        hourly = data.get("hourly", {})
        probs = hourly.get("precipitation_probability")
        if isinstance(probs, list):
            if probs and isinstance(probs[0], (int, float)):
                rain_now_pct = int(probs[0])

            vals = []
            for idx, value in enumerate(probs):
                if idx >= rain_3h_hours:
                    break
                if isinstance(value, (int, float)):
                    vals.append(value)
            if vals:
                rain_3h_pct = int(max(vals))

        # If model says raining now but hourly percentage is missing, show certainty.
        if rain_now_pct is None and raining_now:
            rain_now_pct = 100

        weather = {
            "sun_pct": sun_pct,
            "rain_now_pct": rain_now_pct,
            "rain_3h_pct": rain_3h_pct,
        }
        _dbg(
            "weather sun={} rain={} rain3h={}".format(
                "--" if sun_pct is None else sun_pct,
                "--" if rain_now_pct is None else rain_now_pct,
                "--" if rain_3h_pct is None else rain_3h_pct,
            )
        )
        return weather
    except Exception as exc:
        _dbg("weather fetch failed: {}".format(exc))
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _safe_bme(i2c, address):
    if i2c is None:
        _dbg("BME bus unavailable; skipping sensor 0x{:02X}".format(address))
        return None
    try:
        sensor = BME280(i2c, address)
        _dbg("BME280 detected at 0x{:02X}".format(address))
        return sensor
    except Exception as exc:
        _dbg("BME280 missing/unresponsive at 0x{:02X}: {}".format(address, exc))
        return None


def _safe_ina(i2c, address):
    if i2c is None:
        _dbg("INA bus unavailable; skipping sensor init")
        return None
    try:
        sensor = INA3221(i2c, address=address, shunt_ohms=config.INA3221_SHUNTS)
        _dbg("INA3221 init OK at 0x{:02X}".format(address))
        return sensor
    except Exception as exc:
        _dbg("INA3221 init failed at 0x{:02X}: {}".format(address, exc))
        return None


def _scan_debug(i2c, label, scl_pin, sda_pin, freq):
    if not _debug_enabled():
        return
    try:
        devices = i2c.scan()
        dev_text = ", ".join("0x{:02X}".format(d) for d in devices) if devices else "none"
        _dbg("{} up (SCL=GP{}, SDA=GP{}, {}Hz), devices: {}".format(label, scl_pin, sda_pin, freq, dev_text))
    except Exception:
        _dbg("{} up (SCL=GP{}, SDA=GP{}, {}Hz), scan failed".format(label, scl_pin, sda_pin, freq))


def _safe_i2c(bus_id, scl_pin, sda_pin, freq, prefer_soft=False):
    sck = machine.Pin(scl_pin)
    sda = machine.Pin(sda_pin)

    # Common fix: SoftI2C can be more reliable with some sensor boards/wiring.
    if prefer_soft and SoftI2C is not None:
        try:
            i2c = SoftI2C(scl=sck, sda=sda, freq=freq)
            time.sleep_ms(5)
            _scan_debug(i2c, "SoftI2C", scl_pin, sda_pin, freq)
            return i2c
        except Exception:
            _dbg("SoftI2C init failed (SCL=GP{}, SDA=GP{}, {}Hz)".format(scl_pin, sda_pin, freq))

    try:
        i2c = machine.I2C(
            bus_id,
            scl=sck,
            sda=sda,
            freq=freq,
        )
        time.sleep_ms(5)
        _scan_debug(i2c, "I2C{}".format(bus_id), scl_pin, sda_pin, freq)
        return i2c
    except Exception:
        _dbg("I2C{} init failed (SCL=GP{}, SDA=GP{}, {}Hz)".format(bus_id, scl_pin, sda_pin, freq))

    if SoftI2C is not None:
        try:
            i2c = SoftI2C(scl=sck, sda=sda, freq=freq)
            time.sleep_ms(5)
            _scan_debug(i2c, "SoftI2C", scl_pin, sda_pin, freq)
            return i2c
        except Exception:
            _dbg("SoftI2C fallback failed (SCL=GP{}, SDA=GP{}, {}Hz)".format(scl_pin, sda_pin, freq))

    return None


def _find_ina_address(i2c, preferred_addr):
    if i2c is None:
        return None
    try:
        devices = i2c.scan()
    except Exception:
        return None

    if preferred_addr in devices:
        return preferred_addr

    # INA3221 typically maps to 0x40..0x43 depending address pin strapping.
    for addr in (0x40, 0x41, 0x42, 0x43):
        if addr in devices:
            return addr
    return None


def _probe_ina_bus(candidates, preferred_addr):
    for i2c in candidates:
        addr = _find_ina_address(i2c, preferred_addr)
        if addr is None:
            continue
        sensor = _safe_ina(i2c, addr)
        if sensor is not None:
            return sensor, addr
    return None, None


def _read_bme(sensor):
    if sensor is None:
        return None, None, None
    try:
        sample = sensor.read_dict()
        return sample["temperature_c"], sample["humidity_pct"], sample["pressure_hpa"]
    except Exception as exc:
        _dbg("BME read failed: {}".format(exc))
        return None, None, None


def _read_ina_channel(sensor, channel):
    if sensor is None:
        return None, None
    try:
        sample = sensor.read_channel(channel)
        return sample["bus_v"], sample["current_a"]
    except Exception as exc:
        _dbg("INA read failed ch{}: {}".format(channel, exc))
        return None, None


def _read_ina_all_channels(sensor):
    out = {}
    for ch in (1, 2, 3):
        v, a = _read_ina_channel(sensor, ch)
        out[ch] = (v, a)
    return out


def _avg(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _sample_averaged_readings(internal_bme, external_bme, ina):
    window_ms = int(getattr(config, "SENSOR_SAMPLE_WINDOW_MS", 10_000))
    period_ms = int(getattr(config, "SENSOR_SAMPLE_PERIOD_MS", 500))
    if window_ms <= 0 or period_ms <= 0:
        in_t, in_h, in_p = _read_bme(internal_bme)
        out_t, out_h, out_p = _read_bme(external_bme)
        batt_v, batt_a = _read_ina_channel(ina, 1)
        solar_v, solar_a = _read_ina_channel(ina, 2)
        return in_t, in_h, in_p, out_t, out_h, out_p, batt_v, batt_a, solar_v, solar_a

    start = time.ticks_ms()
    in_t_s, in_h_s, in_p_s = [], [], []
    out_t_s, out_h_s, out_p_s = [], [], []
    batt_v_s, batt_a_s = [], []
    solar_v_s, solar_a_s = [], []

    sample_count = window_ms // period_ms
    if sample_count < 1:
        sample_count = 1

    for idx in range(sample_count):
        in_t, in_h, in_p = _read_bme(internal_bme)
        out_t, out_h, out_p = _read_bme(external_bme)
        batt_v, batt_a = _read_ina_channel(ina, 1)
        solar_v, solar_a = _read_ina_channel(ina, 2)

        in_t_s.append(in_t)
        in_h_s.append(in_h)
        in_p_s.append(in_p)
        out_t_s.append(out_t)
        out_h_s.append(out_h)
        out_p_s.append(out_p)
        batt_v_s.append(batt_v)
        batt_a_s.append(batt_a)
        solar_v_s.append(solar_v)
        solar_a_s.append(solar_a)

        if idx < sample_count - 1:
            time.sleep_ms(period_ms)

    elapsed_ms = time.ticks_diff(time.ticks_ms(), start)
    _dbg("Sensor sample complete ({} samples in {} ms)".format(sample_count, elapsed_ms))

    return (
        _avg(in_t_s),
        _avg(in_h_s),
        _avg(in_p_s),
        _avg(out_t_s),
        _avg(out_h_s),
        _avg(out_p_s),
        _avg(batt_v_s),
        _avg(batt_a_s),
        _avg(solar_v_s),
        _avg(solar_a_s),
    )


def _sleep_low_power(ms):
    # Some MicroPython targets support deepsleep(ms); fallback to lightsleep(ms).
    try:
        machine.deepsleep(ms)
    except AttributeError:
        machine.lightsleep(ms)


def _button_wake_irq(_pin):
    global _button_wake_flag
    _button_wake_flag = True


def _sleep_until_event(total_ms):
    global _button_wake_flag

    if total_ms <= 0:
        return "timer"

    if not bool(getattr(config, "ENABLE_BUTTON_WAKE", True)):
        _sleep_low_power(total_ms)
        return "timer"

    poll_ms = int(getattr(config, "SLEEP_POLL_MS", 1000))
    if poll_ms < 50:
        poll_ms = 50

    pin_mode = machine.Pin(getattr(config, "BUTTON_MODE_PIN", 8), machine.Pin.IN, machine.Pin.PULL_UP)
    pin_servo = machine.Pin(getattr(config, "BUTTON_SERVO_PIN", 9), machine.Pin.IN, machine.Pin.PULL_UP)

    _button_wake_flag = False
    pin_mode.irq(trigger=machine.Pin.IRQ_FALLING, handler=_button_wake_irq)
    pin_servo.irq(trigger=machine.Pin.IRQ_FALLING, handler=_button_wake_irq)

    start = time.ticks_ms()
    try:
        while time.ticks_diff(time.ticks_ms(), start) < total_ms:
            if _button_wake_flag:
                return "button"

            remaining = total_ms - time.ticks_diff(time.ticks_ms(), start)
            chunk = poll_ms if remaining > poll_ms else remaining
            if chunk <= 0:
                break

            try:
                machine.lightsleep(chunk)
            except AttributeError:
                time.sleep_ms(chunk)

            if _button_wake_flag:
                return "button"
    finally:
        pin_mode.irq(handler=None)
        pin_servo.irq(handler=None)

    return "timer"


def _safe_epd():
    global _epd_failed_once, _epd_cached

    if not getattr(config, "ENABLE_EPD", True):
        return None
    if _epd_failed_once:
        return None
    if _debug_enabled() and _epd_cached is not None:
        return _epd_cached

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
            reverse_bits=getattr(config, "EPD_REVERSE_BITS", False),
            clear_on_init=getattr(config, "EPD_CLEAR_ON_INIT", False),
        )
        epd.init()
        if _debug_enabled():
            _epd_cached = epd
        return epd
    except Exception as exc:
        _epd_failed_once = True
        print("EPD init failed (SPI {}): {}".format(spi_id, type(exc).__name__))
        sys.print_exception(exc)
        return None


def _enter_wireless_mode(epd, payload):
    global _web_server_port

    sta_timeout = int(getattr(config, "WIFI_CONNECT_TIMEOUT_MS", 30_000))
    ap_ssid = str(getattr(config, "WIFI_AP_SSID", "BiomeLX-Setup"))

    if epd is not None:
        epd = _safe_epd_draw(epd, draw_wifi_mode, "CONNECTING", "Station", "", _resolve_refresh_mode("quick"))

    wlan = _wifi_connect(timeout_ms=sta_timeout)
    if wlan is not None:
        ip = "?"
        try:
            ip = wlan.ifconfig()[0]
        except Exception:
            pass

        _sync_time_from_web()
        payload["wifi_connected"] = True
        payload["wifi_mode"] = True
        server_ok = _start_web_server() is not None
        if server_ok:
            _web_log("Web UI URL: http://{}:{}/".format(ip, _web_server_port))
            detail = "WEB:{}".format(_web_server_port)
        else:
            _web_log("Wi-Fi up but Web UI failed to start")
            detail = "WEB START FAIL"
        if epd is not None:
            epd = _safe_epd_draw(epd, draw_wifi_mode, "CONNECTED", detail, ip, _resolve_refresh_mode("quick"))
        return epd

    ap = _wifi_start_ap()
    ip = "?"
    if ap is not None:
        try:
            ip = ap.ifconfig()[0]
        except Exception:
            pass
    payload["wifi_connected"] = False
    payload["wifi_mode"] = True
    server_ok = _start_web_server() is not None
    detail = "AP:{} WEB:{}".format(ap_ssid, _web_server_port if _web_server_port is not None else "OFF")
    if not server_ok:
        _web_log("AP active but Web UI failed to start")
        detail = "AP:{} WEB FAIL".format(ap_ssid)
    else:
        _web_log("AP Web UI URL: http://{}:{}/".format(ip, _web_server_port))
    if epd is not None:
        epd = _safe_epd_draw(epd, draw_wifi_mode, "AP MODE", detail, ip, _resolve_refresh_mode("quick"))
    return epd


def _exit_wireless_mode(epd, payload):
    _stop_web_server()
    _wifi_disable_all()
    payload["wifi_connected"] = False
    payload["wifi_mode"] = False
    if epd is not None:
        epd = _safe_epd_draw(epd, draw_action_message, "WIRELESS", "OFF", _resolve_refresh_mode("quick"))
    return epd


def _draw_wireless_status(epd):
    if epd is None:
        return epd

    detail = "WEB:{}".format(_web_server_port if _web_server_port is not None else "OFF")
    try:
        sta = network.WLAN(network.STA_IF) if network is not None else None
        if sta is not None and sta.active() and sta.isconnected():
            ip = sta.ifconfig()[0]
            return _safe_epd_draw(epd, draw_wifi_mode, "CONNECTED", detail, ip, _resolve_refresh_mode("quick"))
    except Exception:
        pass

    try:
        ap = network.WLAN(network.AP_IF) if network is not None else None
        if ap is not None and ap.active():
            ip = ap.ifconfig()[0]
            ap_ssid = str(getattr(config, "WIFI_AP_SSID", "BiomeLX-Setup"))
            return _safe_epd_draw(epd, draw_wifi_mode, "AP MODE", "AP:{} {}".format(ap_ssid, detail), ip, _resolve_refresh_mode("quick"))
    except Exception:
        pass

    return _safe_epd_draw(epd, draw_wifi_mode, "WIFI MODE", detail, "", _resolve_refresh_mode("quick"))


def run_once():
    global _power_saver_mode, _canopy_open, _swallow_wake_button_press, _last_wake_reason, _wireless_mode

    _dbg("run_once start")
    _recover_config_from_backup_if_needed()
    _ensure_config_defaults_loaded()
    _canopy_open = _load_canopy_state()

    bme_i2c_id = getattr(config, "BME_I2C_ID", getattr(config, "I2C_ID", 1))
    bme_i2c_scl = getattr(config, "BME_I2C_PIN_SCL", getattr(config, "I2C_PIN_SCL", 27))
    bme_i2c_sda = getattr(config, "BME_I2C_PIN_SDA", getattr(config, "I2C_PIN_SDA", 26))
    bme_i2c_freq = getattr(config, "BME_I2C_FREQ", getattr(config, "I2C_FREQ", 400_000))

    bme_i2c = _safe_i2c(
        bme_i2c_id,
        bme_i2c_scl,
        bme_i2c_sda,
        bme_i2c_freq,
        prefer_soft=bool(getattr(config, "BME_PREFER_SOFT_I2C", False)),
    )

    ina_i2c_id = getattr(config, "INA_I2C_ID", 0)
    ina_i2c_scl = getattr(config, "INA_I2C_PIN_SCL", 1)
    ina_i2c_sda = getattr(config, "INA_I2C_PIN_SDA", 0)
    ina_i2c_freq = getattr(config, "INA_I2C_FREQ", getattr(config, "I2C_FREQ", 400_000))
    preferred_ina_addr = getattr(config, "INA3221_ADDR", 0x40)

    internal_bme = _safe_bme(bme_i2c, config.BME280_INTERNAL_ADDR)
    external_bme = _safe_bme(bme_i2c, config.BME280_EXTERNAL_ADDR)

    # Read BME once before any INA bus remap to preserve first sample on BME wiring.
    pre_in_t, pre_in_h, pre_in_p = _read_bme(internal_bme)
    pre_out_t, pre_out_h, pre_out_p = _read_bme(external_bme)

    # Initialize INA bus after BME reads when both use same controller with different pins.
    if (
        ina_i2c_id == bme_i2c_id
        and ina_i2c_scl == bme_i2c_scl
        and ina_i2c_sda == bme_i2c_sda
        and ina_i2c_freq == bme_i2c_freq
    ):
        ina_i2c = bme_i2c
        _dbg("Reusing I2C{} GP{}/GP{} for BME + INA".format(ina_i2c_id, ina_i2c_scl, ina_i2c_sda))
    else:
        if ina_i2c_id == bme_i2c_id:
            _dbg("Switching I2C{} from BME GP{}/GP{} to INA GP{}/GP{}".format(
                ina_i2c_id, bme_i2c_scl, bme_i2c_sda, ina_i2c_scl, ina_i2c_sda
            ))
        ina_i2c = _safe_i2c(
            ina_i2c_id,
            ina_i2c_scl,
            ina_i2c_sda,
            ina_i2c_freq,
            prefer_soft=bool(getattr(config, "INA_PREFER_SOFT_I2C", False)),
        )

    # Probe configured INA bus first.
    ina, ina_addr = _probe_ina_bus((ina_i2c,), preferred_ina_addr)

    # Only if explicitly enabled, probe common fallback wirings one-by-one.
    if ina is None and bool(getattr(config, "INA_ENABLE_FALLBACK_PROBE", False)):
        fallback_specs = (
            (0, 1, 0),
            (0, 5, 4),
            (1, 27, 26),
        )
        for bus_id, scl_pin, sda_pin in fallback_specs:
            if (
                bus_id == ina_i2c_id
                and scl_pin == ina_i2c_scl
                and sda_pin == ina_i2c_sda
            ):
                continue
            if (
                bus_id == bme_i2c_id
                and scl_pin == bme_i2c_scl
                and sda_pin == bme_i2c_sda
            ):
                probe_i2c = bme_i2c
            else:
                probe_i2c = _safe_i2c(bus_id, scl_pin, sda_pin, ina_i2c_freq)

            ina, ina_addr = _probe_ina_bus((probe_i2c,), preferred_ina_addr)
            if ina is not None:
                break

    if _debug_enabled():
        if ina is None:
            print("INA3221 not found on probed I2C buses")
        else:
            print("INA3221 detected at 0x{:02X}".format(ina_addr))

    servo_pwm_freq = int(getattr(config, "SERVO_PWM_FREQ", 50))
    # Standard analog hobby servos are designed for ~50 Hz.
    if servo_pwm_freq != 50:
        _dbg("Forcing SERVO_PWM_FREQ={} -> 50 for stable torque".format(servo_pwm_freq))
        servo_pwm_freq = 50

    servo = DualServo(
        config.SERVO_PIN_A,
        config.SERVO_PIN_B,
        freq=servo_pwm_freq,
        min_us=int(getattr(config, "SERVO_MIN_PULSE_US", 1000)),
        max_us=int(getattr(config, "SERVO_MAX_PULSE_US", 2000)),
        span_deg=int(getattr(config, "SERVO_SPAN_DEG", 180)),
    )
    _set_servo_rail(False)

    epd = _safe_epd()
    wake_refresh_mode = _resolve_refresh_mode("full" if _last_wake_reason == "timer" else "quick")

    (
        in_t,
        in_h,
        in_p,
        out_t,
        out_h,
        out_p,
        batt_v,
        batt_a,
        solar_v,
        solar_a,
    ) = _sample_averaged_readings(internal_bme, external_bme, ina)

    # Keep pre-remap BME sample if averaging failed to yield valid values.
    if in_t is None:
        in_t = pre_in_t
    if in_h is None:
        in_h = pre_in_h
    if in_p is None:
        in_p = pre_in_p
    if out_t is None:
        out_t = pre_out_t
    if out_h is None:
        out_h = pre_out_h
    if out_p is None:
        out_p = pre_out_p

    batt_ch = int(getattr(config, "INA_BATT_CHANNEL", 1))
    solar_ch = int(getattr(config, "INA_SOLAR_CHANNEL", 2))
    batt_scale = float(getattr(config, "INA_BATT_VOLTAGE_SCALE", 1.0))
    solar_scale = float(getattr(config, "INA_SOLAR_VOLTAGE_SCALE", 1.0))

    # Re-read mapped channels so displayed values always follow current mapping.
    batt_v, batt_a = _read_ina_channel(ina, batt_ch)
    solar_v, solar_a = _read_ina_channel(ina, solar_ch)
    if batt_v is not None:
        batt_v *= batt_scale
    if solar_v is not None:
        solar_v *= solar_scale

    if _debug_enabled() and ina is not None:
        ch_map = _read_ina_all_channels(ina)
        for ch in (1, 2, 3):
            v, a = ch_map[ch]
            _dbg(
                "INA CH{} raw: V={} A={}".format(
                    ch,
                    "--" if v is None else "{:.3f}".format(v),
                    "--" if a is None else "{:.4f}".format(a),
                )
            )

    weather = _read_weather()
    weather_sun_pct = None
    weather_rain_pct = None
    weather_rain_3h_pct = None
    if weather is not None:
        weather_sun_pct = weather.get("sun_pct")
        weather_rain_pct = weather.get("rain_now_pct")
        weather_rain_3h_pct = weather.get("rain_3h_pct")

    desired_open = _apply_canopy_rules(weather_rain_pct, weather_rain_3h_pct)
    _set_canopy_state(servo, desired_open, force=True, motion_mode="simple")
    canopy_state = "open" if _canopy_open else "closed"

    payload = {
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
        "weather_sun_pct": weather_sun_pct,
        "weather_rain_pct": weather_rain_pct,
        "weather_rain_3h_pct": weather_rain_3h_pct,
        "time_hm": _local_time_hm(),
        "last_update_hm": _local_time_hm(),
        "weather_phase": _weather_phase_label(),
        "wifi_connected": _wifi_is_connected(),
        "wifi_mode": _wireless_mode,
        "power_saver": _power_saver_mode,
    }

    _append_log_row(payload)

    _dbg(
        "IN  T={}C H={}% P={}hPa".format(
            "--" if in_t is None else "{:.1f}".format(in_t),
            "--" if in_h is None else "{:.0f}".format(in_h),
            "--" if in_p is None else "{:.0f}".format(in_p),
        )
    )
    _dbg(
        "OUT T={}C H={}% P={}hPa".format(
            "--" if out_t is None else "{:.1f}".format(out_t),
            "--" if out_h is None else "{:.0f}".format(out_h),
            "--" if out_p is None else "{:.0f}".format(out_p),
        )
    )
    _dbg(
        "INA batt(ch{}): V={} A={}".format(
            batt_ch,
            "--" if batt_v is None else "{:.2f}".format(batt_v),
            "--" if batt_a is None else "{:.3f}".format(batt_a),
        )
    )
    _dbg(
        "INA solar(ch{}): V={} A={}".format(
            solar_ch,
            "--" if solar_v is None else "{:.2f}".format(solar_v),
            "--" if solar_a is None else "{:.3f}".format(solar_a),
        )
    )

    if epd is not None:
        _dbg("EPD draw start")
        epd = _safe_epd_draw(epd, draw_status, payload, wake_refresh_mode)
    else:
        _dbg("EPD unavailable; skipped draw")

    # Button interaction window.
    pin_mode = machine.Pin(getattr(config, "BUTTON_MODE_PIN", 8), machine.Pin.IN, machine.Pin.PULL_UP)
    pin_servo = machine.Pin(getattr(config, "BUTTON_SERVO_PIN", 9), machine.Pin.IN, machine.Pin.PULL_UP)
    debounce_ms = int(getattr(config, "BUTTON_DEBOUNCE_MS", 250))
    action_display_ms = int(getattr(config, "BUTTON_ACTION_DISPLAY_MS", 5000))
    idle_timeout_ms = int(getattr(config, "AWAKE_IDLE_TIMEOUT_MS", 60000))

    last_activity = time.ticks_ms()
    last_mode_press = 0
    last_servo_press = 0
    prev_mode_pressed = False
    prev_servo_pressed = False

    # If wake was caused by a button edge, require release before accepting actions.
    if _swallow_wake_button_press:
        while _read_button(pin_mode) or _read_button(pin_servo):
            time.sleep_ms(20)
        _swallow_wake_button_press = False

    action_until = None

    if _wireless_mode:
        epd = _enter_wireless_mode(epd, payload)

    while True:
        now = time.ticks_ms()
        payload["time_hm"] = _local_time_hm()
        payload["weather_phase"] = _weather_phase_label()

        if (not _wireless_mode) and time.ticks_diff(now, last_activity) >= idle_timeout_ms:
            break

        if _wireless_mode:
            _poll_web_server_once()

        mode_pressed = _read_button(pin_mode)
        servo_pressed = _read_button(pin_servo)
        mode_edge = mode_pressed and (not prev_mode_pressed)
        servo_edge = servo_pressed and (not prev_servo_pressed)
        prev_mode_pressed = mode_pressed
        prev_servo_pressed = servo_pressed

        if mode_edge and time.ticks_diff(now, last_mode_press) >= debounce_ms:
            last_mode_press = now
            last_activity = now
            _wireless_mode = not _wireless_mode
            payload["wifi_mode"] = _wireless_mode
            payload["power_saver"] = not _wireless_mode
            if _wireless_mode:
                epd = _enter_wireless_mode(epd, payload)
                action_until = None
            else:
                epd = _exit_wireless_mode(epd, payload)
                action_until = time.ticks_add(now, action_display_ms)

        if servo_edge and time.ticks_diff(now, last_servo_press) >= debounce_ms:
            last_servo_press = now
            last_activity = now
            next_open = not _canopy_open
            if epd is not None:
                msg = "OPENING" if next_open else "CLOSING"
                epd = _safe_epd_draw(epd, draw_action_message, msg, "", _resolve_refresh_mode("quick"))
            _set_canopy_state(servo, next_open, force=False)
            canopy_state = "open" if _canopy_open else "closed"
            payload["state"] = canopy_state
            _dbg("Servo move complete")

            # E-ink refresh only after movement is fully complete.
            if epd is not None:
                if _wireless_mode:
                    epd = _draw_wireless_status(epd)
                else:
                    payload["last_update_hm"] = _local_time_hm()
                    epd = _safe_epd_draw(epd, draw_status, payload, _resolve_refresh_mode("quick"))
            action_until = None

        if _wireless_mode:
            payload["wifi_connected"] = _wifi_is_connected()

        if epd is not None and (not _wireless_mode) and action_until is not None and time.ticks_diff(now, action_until) >= 0:
            epd = _safe_epd_draw(epd, draw_status, payload, _resolve_refresh_mode("quick"))
            action_until = None

        time.sleep_ms(50)

    _stop_web_server()

    if epd is not None:
        if _debug_enabled():
            _dbg("EPD kept awake for debug")
        else:
            epd.sleep()
            _dbg("EPD sleep")

    servo.deinit()
    _dbg("run_once end")


def main():
    global _swallow_wake_button_press, _last_wake_reason, _wireless_mode

    _ensure_log_file()

    while True:
        try:
            run_once()
        except Exception as exc:
            print("run_once failed: {}".format(exc))
            sys.print_exception(exc)
            time.sleep_ms(1000)

        if _debug_enabled():
            time.sleep_ms(getattr(config, "DEBUG_LOOP_DELAY_MS", 2000))
            continue

        if _wireless_mode:
            time.sleep_ms(200)
            continue

        time.sleep_ms(200)
        if getattr(config, "ENABLE_LOW_POWER_SLEEP", True):
            wake_reason = _sleep_until_event(_wake_interval_ms())
            _last_wake_reason = wake_reason
            if wake_reason == "button":
                _dbg("Woke by button")
                _swallow_wake_button_press = True


main()
