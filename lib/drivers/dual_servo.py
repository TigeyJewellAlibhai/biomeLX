"""Dual-servo control where both channels always move together."""

from machine import Pin, PWM
import time


class DualServo:
    def __init__(self, pin_a, pin_b, freq=50, min_us=1000, max_us=2000, span_deg=180):
        self._pwm_a = PWM(Pin(pin_a))
        self._pwm_b = PWM(Pin(pin_b))
        self._pwm_a.freq(freq)
        self._pwm_b.freq(freq)

        self._freq = int(freq)
        self._period_ns = int(1_000_000_000 // self._freq)
        self._min_us = int(min_us)
        self._max_us = int(max_us)
        self._span_deg = float(span_deg)
        self._current_angle = 0.0
        self._last_pulse_ns = None

    def _angle_to_pulse_ns(self, angle):
        if angle < 0:
            angle = 0
        elif angle > self._span_deg:
            angle = self._span_deg

        pulse_us = self._min_us + ((self._max_us - self._min_us) * (float(angle) / self._span_deg))
        pulse_ns = int(pulse_us * 1000)

        # Keep pulse within current PWM period for valid duty_ns writes.
        if pulse_ns < 0:
            pulse_ns = 0
        elif pulse_ns > self._period_ns:
            pulse_ns = self._period_ns
        return pulse_ns

    def set_angle(self, angle):
        if angle < 0:
            angle = 0
        elif angle > self._span_deg:
            angle = self._span_deg

        pulse_ns = self._angle_to_pulse_ns(angle)
        if self._last_pulse_ns != pulse_ns:
            # Use hardware PWM pulse width API from MicroPython docs.
            self._pwm_a.duty_ns(pulse_ns)
            self._pwm_b.duty_ns(pulse_ns)
            self._last_pulse_ns = pulse_ns
        self._current_angle = float(angle)

    @property
    def current_angle(self):
        return self._current_angle

    @staticmethod
    def _smoothstep(t):
        # Classic S-curve easing for gentle start/end.
        return t * t * (3.0 - (2.0 * t))

    def move_angle(
        self,
        target_angle,
        total_ms=8000,
        step_ms=40,
        ramp_strength=0.7,
        breakaway_deg=0.0,
        breakaway_hold_ms=0,
        min_step_deg=0.0,
    ):
        if target_angle < 0:
            target_angle = 0
        elif target_angle > self._span_deg:
            target_angle = self._span_deg

        start = self._current_angle
        delta = float(target_angle) - start
        if abs(delta) < 0.01:
            self.set_angle(target_angle)
            return

        if total_ms <= 0 or step_ms <= 0:
            self.set_angle(target_angle)
            return

        if ramp_strength < 0.0:
            ramp_strength = 0.0
        elif ramp_strength > 1.0:
            ramp_strength = 1.0

        steps = total_ms // step_ms
        if steps < 1:
            steps = 1

        if min_step_deg > 0:
            max_steps_by_angle = int(abs(delta) / float(min_step_deg))
            if max_steps_by_angle < 1:
                max_steps_by_angle = 1
            if steps > max_steps_by_angle:
                steps = max_steps_by_angle

        # Initial push helps break static friction on heavy loads.
        if breakaway_deg > 0 and abs(delta) > breakaway_deg:
            push_angle = start + (breakaway_deg if delta > 0 else -breakaway_deg)
            self.set_angle(push_angle)
            if breakaway_hold_ms > 0:
                time.sleep_ms(int(breakaway_hold_ms))

        for i in range(1, steps + 1):
            t = i / steps
            # Blend linear with smoothstep based on ramp strength.
            eased_t = t + (ramp_strength * (self._smoothstep(t) - t))
            self.set_angle(start + (delta * eased_t))
            time.sleep_ms(step_ms)

        self.set_angle(target_angle)

    def deinit(self):
        self._pwm_a.deinit()
        self._pwm_b.deinit()
