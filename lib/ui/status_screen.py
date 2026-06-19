"""Status screen rendering using a classic 5x7 GFX-style text renderer."""

from lib.ui.gfx_text import GFXText


def _fmt(value, fmt):
    if value is None:
        return "--"
    return fmt % value


def _fmt_unit(value, fmt, unit):
    if value is None:
        return "--{}".format(unit)
    return (fmt % value) + unit


def _normalize_state(raw_state):
    if raw_state is None:
        return "--"
    state = str(raw_state).strip().lower()
    if state.startswith("open"):
        return "OPEN"
    if state.startswith("close"):
        return "CLOSED"
    return state.upper()


def _draw_env_box(text, fb, x, y, w, h, title, t_c, h_pct, p_hpa):
    fb.rect(x, y, w, h, 0)
    text.draw_center(title, x, y + 8, w, color=0, scale=1, spacing=1)
    text.draw_center(_fmt_unit(t_c, "%.0f", "C"), x, y + 24, w, color=0, scale=2, spacing=1)
    text.draw_center(_fmt_unit(h_pct, "%.0f", "%"), x, y + 46, w, color=0, scale=1, spacing=1)
    text.draw_center(_fmt_unit(p_hpa, "%.0f", "HPA"), x, y + 62, w, color=0, scale=1, spacing=1)


def draw_status(epd, payload, refresh_mode="full"):
    fb = epd.framebuf
    fb.fill(1)
    text = GFXText(fb)

    text.draw("BIOMELX", 4, 4, color=0, scale=2, spacing=1)
    text.draw_right(_normalize_state(payload.get("state", "--")), 66, 10, 54, color=0, scale=1, spacing=1)

    _draw_env_box(
        text,
        fb,
        2,
        28,
        57,
        92,
        "INTERNAL",
        payload.get("in_t_c"),
        payload.get("in_h_pct"),
        payload.get("in_p_hpa"),
    )
    _draw_env_box(
        text,
        fb,
        63,
        28,
        57,
        92,
        "EXTERNAL",
        payload.get("out_t_c"),
        payload.get("out_h_pct"),
        payload.get("out_p_hpa"),
    )

    fb.rect(2, 124, 118, 56, 0)
    text.draw_center(_fmt_unit(payload.get("batt_v"), "%.1f", "V"), 2, 136, 58, color=0, scale=2, spacing=1)
    text.draw_center(_fmt_unit(payload.get("solar_v"), "%.1f", "V"), 60, 136, 60, color=0, scale=2, spacing=1)
    text.draw_center("BATT", 2, 160, 58, color=0, scale=1, spacing=1)
    text.draw_center("SOLAR", 60, 160, 60, color=0, scale=1, spacing=1)

    fb.rect(2, 184, 118, 64, 0)
    fb.vline(66, 184, 64, 0)

    # Left side: local time, weather phase and percentages.
    time_hm = payload.get("time_hm") or "--:--"
    phase = payload.get("weather_phase", "--")
    text.draw("{} {}".format(time_hm, phase), 6, 188, color=0, scale=1, spacing=1)
    text.draw("SUN {}%".format(_fmt(payload.get("weather_sun_pct"), "%.0f")), 6, 202, color=0, scale=1, spacing=1)
    text.draw("RAIN {}%".format(_fmt(payload.get("weather_rain_pct"), "%.0f")), 6, 216, color=0, scale=1, spacing=1)
    text.draw("R3H {}%".format(_fmt(payload.get("weather_rain_3h_pct"), "%.0f")), 6, 230, color=0, scale=1, spacing=1)

    # Right side: last sensor refresh time.
    last_update_hm = payload.get("last_update_hm") or "--:--"
    text.draw_center("LAST", 68, 192, 51, color=0, scale=1, spacing=1)
    text.draw_center("UPDATE", 68, 204, 51, color=0, scale=1, spacing=1)
    text.draw_center(last_update_hm, 68, 220, 51, color=0, scale=1, spacing=1)

    epd.display_frame(refresh_mode=refresh_mode)


def draw_action_message(epd, line1, line2="", refresh_mode="full"):
    fb = epd.framebuf
    fb.fill(1)
    text = GFXText(fb)

    text.draw_center("BIOMELX", 2, 24, 118, color=0, scale=1, spacing=1)
    text.draw_center(line1, 2, 88, 118, color=0, scale=2, spacing=1)
    if line2:
        text.draw_center(line2, 2, 136, 118, color=0, scale=1, spacing=1)

    epd.display_frame(refresh_mode=refresh_mode)


def draw_wifi_mode(epd, status, detail="", ip_addr="", refresh_mode="full"):
    fb = epd.framebuf
    fb.fill(1)
    text = GFXText(fb)

    text.draw_center("BIOMELX", 2, 14, 118, color=0, scale=1, spacing=1)
    text.draw_center("WIFI MODE", 2, 44, 118, color=0, scale=2, spacing=1)
    text.draw_center(str(status or "IDLE"), 2, 96, 118, color=0, scale=1, spacing=1)

    if detail:
        text.draw_center(str(detail), 2, 122, 118, color=0, scale=1, spacing=1)

    if ip_addr:
        text.draw_center("IP", 2, 170, 118, color=0, scale=1, spacing=1)
        text.draw_center(str(ip_addr), 2, 186, 118, color=0, scale=1, spacing=1)

    text.draw_center("BTN1 EXIT  BTN2 TOGGLE", 2, 232, 118, color=0, scale=1, spacing=1)
    epd.display_frame(refresh_mode=refresh_mode)
