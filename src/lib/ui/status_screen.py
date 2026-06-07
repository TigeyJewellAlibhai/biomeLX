"""Render helpers for the BiomeLX status display."""


def _fmt(value, fmt):
    if value is None:
        return "--"
    return fmt % value


def draw_status(epd, payload):
    fb = epd.framebuf
    fb.fill(1)

    fb.text("BiomeLX", 4, 4, 0)
    fb.text("Status: %s" % payload.get("state", "--"), 4, 20, 0)

    fb.text("IN  T:%sC" % _fmt(payload.get("in_t_c"), "%.1f"), 4, 44, 0)
    fb.text("IN  H:%s%%" % _fmt(payload.get("in_h_pct"), "%.0f"), 4, 56, 0)
    fb.text("IN  P:%shPa" % _fmt(payload.get("in_p_hpa"), "%.0f"), 4, 68, 0)

    fb.text("OUT T:%sC" % _fmt(payload.get("out_t_c"), "%.1f"), 4, 92, 0)
    fb.text("OUT H:%s%%" % _fmt(payload.get("out_h_pct"), "%.0f"), 4, 104, 0)
    fb.text("OUT P:%shPa" % _fmt(payload.get("out_p_hpa"), "%.0f"), 4, 116, 0)

    fb.text("Batt V:%s" % _fmt(payload.get("batt_v"), "%.2f"), 4, 140, 0)
    fb.text("Batt A:%s" % _fmt(payload.get("batt_a"), "%.2f"), 4, 152, 0)
    fb.text("Solar V:%s" % _fmt(payload.get("solar_v"), "%.2f"), 4, 164, 0)
    fb.text("Solar A:%s" % _fmt(payload.get("solar_a"), "%.2f"), 4, 176, 0)

    fb.text("Weather: pending", 4, 204, 0)
    fb.text("Sleep: 5 min", 4, 220, 0)

    epd.display_frame()
