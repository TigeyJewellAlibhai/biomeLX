"""Waveshare 2.13in V4 monochrome e-ink driver with compatibility alias."""

import framebuf
import time
from machine import Pin, SPI

try:
    from machine import SoftSPI
except ImportError:
    SoftSPI = None

EPD_WIDTH = 122
EPD_HEIGHT = 250


class EPD2in13V4:
    def __init__(
        self,
        spi_id,
        pin_sck,
        pin_mosi,
        pin_cs,
        pin_dc,
        pin_rst,
        pin_busy,
        busy_active=1,
        hard_spi_baud=4_000_000,
        soft_spi_baud=2_000_000,
        prefer_soft_spi=False,
        rotate_180=False,
        reverse_bits=False,
        clear_on_init=False,
    ):
        self.width = EPD_WIDTH
        self.height = EPD_HEIGHT
        self._busy_active = 1 if busy_active else 0
        self._rotate_180 = bool(rotate_180)
        self._reverse_bits = bool(reverse_bits)
        self._clear_on_init = bool(clear_on_init)
        # Width is 122px, so each row needs ceil(122/8)=16 bytes.
        self._line_bytes = (self.width + 7) // 8
        self.buffer = bytearray(self._line_bytes * self.height)
        # Force byte-aligned rows (128px stride) so row starts in framebuf match panel RAM rows.
        self.framebuf = framebuf.FrameBuffer(
            self.buffer,
            self.width,
            self.height,
            framebuf.MONO_HMSB,
            self._line_bytes * 8,
        )

        sck_pin = Pin(pin_sck)
        mosi_pin = Pin(pin_mosi)

        spi_error = None
        spi = None

        if prefer_soft_spi and SoftSPI is not None:
            try:
                spi = SoftSPI(
                    baudrate=soft_spi_baud,
                    polarity=0,
                    phase=0,
                    sck=sck_pin,
                    mosi=mosi_pin,
                    miso=Pin(pin_busy),
                )
            except Exception as exc:
                spi_error = exc

        # Variant 1: ports that accept explicit SCK/MOSI pin assignment.
        if spi is None:
            try:
                spi = SPI(
                    spi_id,
                    baudrate=hard_spi_baud,
                    polarity=0,
                    phase=0,
                    sck=sck_pin,
                    mosi=mosi_pin,
                )
            except Exception as exc:
                spi_error = exc

        # Variant 2: ports with fixed hardware-SPI pins (no sck/mosi kwargs).
        if spi is None:
            try:
                spi = SPI(
                    spi_id,
                    baudrate=hard_spi_baud,
                    polarity=0,
                    phase=0,
                )
            except Exception as exc:
                spi_error = exc

        # Variant 3: software SPI fallback when available.
        if spi is None and SoftSPI is not None:
            try:
                spi = SoftSPI(
                    baudrate=soft_spi_baud,
                    polarity=0,
                    phase=0,
                    sck=sck_pin,
                    mosi=mosi_pin,
                    miso=Pin(pin_busy),
                )
            except Exception as exc:
                spi_error = exc

        if spi is None:
            raise ValueError("SPI init failed for spi_id {}: {}".format(spi_id, spi_error))

        self.spi = spi

        try:
            self.cs = Pin(pin_cs, Pin.OUT)
            self.dc = Pin(pin_dc, Pin.OUT)
            self.rst = Pin(pin_rst, Pin.OUT)
            self.busy = Pin(pin_busy, Pin.IN)
        except Exception as exc:
            raise ValueError(
                "EPD control pin init failed (cs={}, dc={}, rst={}, busy={}): {}".format(
                    pin_cs, pin_dc, pin_rst, pin_busy, exc
                )
            )

        self.cs.value(1)

    @staticmethod
    def _bit_reverse8(b):
        b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4)
        b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
        b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
        return b

    def _send_command(self, command):
        self.dc.value(0)
        self.cs.value(0)
        self.spi.write(bytes((command,)))
        self.cs.value(1)

    def _send_data(self, data):
        self.dc.value(1)
        self.cs.value(0)
        if isinstance(data, int):
            self.spi.write(bytes((data,)))
        else:
            self.spi.write(data)
        self.cs.value(1)

    def _wait_until_idle(self, timeout_ms=5_000):
        start = time.ticks_ms()
        while self.busy.value() == self._busy_active:
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("EPD busy timeout")
            time.sleep_ms(10)

    def _reset(self):
        self.rst.value(1)
        time.sleep_ms(200)
        self.rst.value(0)
        time.sleep_ms(5)
        self.rst.value(1)
        time.sleep_ms(200)

    def _set_window(self, x_start, y_start, x_end, y_end):
        self._send_command(0x44)
        self._send_data(x_start >> 3)
        self._send_data(x_end >> 3)

        self._send_command(0x45)
        self._send_data(y_start & 0xFF)
        self._send_data((y_start >> 8) & 0xFF)
        self._send_data(y_end & 0xFF)
        self._send_data((y_end >> 8) & 0xFF)

    def _set_cursor(self, x, y):
        self._send_command(0x4E)
        self._send_data(x >> 3)

        self._send_command(0x4F)
        self._send_data(y & 0xFF)
        self._send_data((y >> 8) & 0xFF)

    def _build_panel_buffer(self):
        w = self.width
        h = self.height
        lb = self._line_bytes
        out = bytearray((0xFF for _ in range(lb * h)))

        # Pack output exactly as panel RAM expects: horizontal bytes, MSB first.
        for y in range(h):
            row = y * lb
            for xb in range(lb):
                b = 0xFF
                for bit in range(8):
                    x = (xb << 3) + bit
                    if x >= w:
                        continue

                    if self._rotate_180:
                        sx = w - 1 - x
                        sy = h - 1 - y
                    else:
                        sx = x
                        sy = y

                    # FrameBuffer uses 1=white, 0=black.
                    if self.framebuf.pixel(sx, sy):
                        b |= 0x80 >> bit
                    else:
                        b &= ~(0x80 >> bit)
                if self._reverse_bits:
                    b = self._bit_reverse8(b)
                out[row + xb] = b

        return out

    def init(self):
        # Waveshare 2.13in V4 initialization sequence.
        self._reset()
        self._wait_until_idle()

        self._send_command(0x12)
        self._wait_until_idle()

        self._send_command(0x01)
        self._send_data(0xF9)
        self._send_data(0x00)
        self._send_data(0x00)

        self._send_command(0x11)
        self._send_data(0x03)

        self._set_window(0, 0, self.width - 1, self.height - 1)
        self._set_cursor(0, 0)

        self._send_command(0x3C)
        self._send_data(0x05)

        self._send_command(0x21)
        self._send_data(0x00)
        self._send_data(0x80)

        self._send_command(0x18)
        self._send_data(0x80)

        self._wait_until_idle()

        if self._clear_on_init:
            self.clear(0xFF)

    def clear(self, color=0xFF):
        self._set_window(0, 0, self.width - 1, self.height - 1)
        self._set_cursor(0, 0)
        self._send_command(0x24)
        total = len(self.buffer)
        chunk = bytes((color,)) * 64
        blocks, rem = divmod(total, 64)
        for _ in range(blocks):
            self._send_data(chunk)
        if rem:
            self._send_data(bytes((color,)) * rem)
        self._refresh()

    def _refresh(self):
        self._send_command(0x22)
        self._send_data(0xF7)
        self._send_command(0x20)
        self._wait_until_idle()

    def _refresh_fast(self):
        self._send_command(0x22)
        self._send_data(0xC7)
        self._send_command(0x20)
        self._wait_until_idle()

    def display_frame(self, refresh_mode="full"):
        self._set_window(0, 0, self.width - 1, self.height - 1)
        self._set_cursor(0, 0)
        self._send_command(0x24)
        self._send_data(self._build_panel_buffer())
        if refresh_mode == "quick":
            self._refresh_fast()
        else:
            self._refresh()

    def sleep(self):
        self._send_command(0x10)
        self._send_data(0x01)
        time.sleep_ms(50)


# Backward-compatible alias for existing imports.
EPD2in13V2 = EPD2in13V4
