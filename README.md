# BiomeLX Firmware

MicroPython firmware for the BiomeLX greenhouse controller.

## Current Scope

This first scaffold release includes:

- Project structure for deployable MicroPython code under `.mpyFiles/`
- Waveshare 2.13in e-ink driver (V2-style SSD1680 command flow)
- Dual-servo driver (both servos always move together)
- BME280 driver (internal + external sensor support)
- INA3221 driver (battery/solar channels)
- Main loop that renders placeholders and sleeps for 5 minutes

It is designed to run even when some sensors are not wired yet.

## Folder Layout

```
.mpyFiles/
	boot.py
	config.py
	main.py
	lib/
		drivers/
			bme280.py
			dual_servo.py
			epd_2in13_v2.py
			ina3221.py
		ui/
			status_screen.py
```

Development source is currently mirrored in `src/`, and `.mpyFiles/` is the extension upload target.

## Hardware Mapping

Configured in `.mpyFiles/config.py`:

- Servos: GP14, GP15
- E-ink SPI0: SCK=GP18, MOSI=GP19, CS=GP17, DC=GP16, RST=GP13, BUSY=GP12
- I2C1 default: SDA=GP26, SCL=GP27 (update if your wiring differs)

Default I2C addresses:

- BME280 internal: `0x76`
- BME280 external: `0x77`
- INA3221: `0x40`

## Runtime Behavior

`.mpyFiles/main.py` does this on each wake cycle:

1. Bring up I2C/SPI and available sensors
2. Read sensor values (missing hardware falls back to placeholders)
3. Draw status to e-ink display
4. Put display to sleep
5. Enter low-power sleep for `WAKE_INTERVAL_MS` (default 5 minutes)

## Deploy Example (mpremote)

Copy all files in `.mpyFiles/` to your board root:

```bash
mpremote connect auto fs cp -r .mpyFiles/* :
mpremote connect auto reset
```

If your shell does not expand `*`, copy directories/files separately.

## Notes

- On some MicroPython ports, `machine.deepsleep(ms)` is unavailable; code falls back to `machine.lightsleep(ms)`.
- Weather and battery/solar refinement are placeholders for now and ready to be extended.
