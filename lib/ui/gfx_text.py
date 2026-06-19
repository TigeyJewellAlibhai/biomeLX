"""Lightweight bitmap text renderer using a 5x7 embedded font."""

from lib.ui.gfx_font_5x7 import FONT_5X7


class GFXText:
    def __init__(self, framebuffer):
        self.fb = framebuffer

    def _glyph(self, ch):
        return FONT_5X7.get(ch, FONT_5X7["?"])

    def measure(self, text, scale=1, spacing=1, uppercase=True):
        if uppercase:
            text = text.upper()
        if not text:
            return 0
        char_w = 5 * scale
        return (len(text) * char_w) + ((len(text) - 1) * spacing)

    def draw(self, text, x, y, color=0, scale=1, spacing=1, uppercase=True):
        if uppercase:
            text = text.upper()
        cx = x
        for ch in text:
            glyph = self._glyph(ch)
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        if scale == 1:
                            self.fb.pixel(cx + col, y + row, color)
                        else:
                            self.fb.fill_rect(cx + (col * scale), y + (row * scale), scale, scale, color)
            cx += (5 * scale) + spacing

    def draw_center(self, text, x, y, width, color=0, scale=1, spacing=1, uppercase=True):
        tw = self.measure(text, scale=scale, spacing=spacing, uppercase=uppercase)
        tx = x + ((width - tw) // 2)
        if tx < x:
            tx = x
        self.draw(text, tx, y, color=color, scale=scale, spacing=spacing, uppercase=uppercase)

    def draw_right(self, text, x, y, width, color=0, scale=1, spacing=1, uppercase=True):
        tw = self.measure(text, scale=scale, spacing=spacing, uppercase=uppercase)
        tx = x + width - tw
        if tx < x:
            tx = x
        self.draw(text, tx, y, color=color, scale=scale, spacing=spacing, uppercase=uppercase)
