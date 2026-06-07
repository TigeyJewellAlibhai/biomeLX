"""Dual-servo control where both channels always move together."""

from machine import Pin, PWM


class DualServo:
    def __init__(self, pin_a, pin_b, freq=50, min_us=500, max_us=2500, span_deg=180):
        self._pwm_a = PWM(Pin(pin_a))
        self._pwm_b = PWM(Pin(pin_b))
        self._pwm_a.freq(freq)
        self._pwm_b.freq(freq)

        self._min_us = min_us
        self._max_us = max_us
        self._span_deg = span_deg

    def _angle_to_duty_u16(self, angle):
        if angle < 0:
            angle = 0
        elif angle > self._span_deg:
            angle = self._span_deg

        pulse_us = self._min_us + (self._max_us - self._min_us) * angle / self._span_deg
        period_us = 1_000_000 / 50
        return int((pulse_us / period_us) * 65535)

    def set_angle(self, angle):
        duty = self._angle_to_duty_u16(angle)
        self._pwm_a.duty_u16(duty)
        self._pwm_b.duty_u16(duty)

    def deinit(self):
        self._pwm_a.deinit()
        self._pwm_b.deinit()
