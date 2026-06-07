"""Hardware and firmware configuration for BiomeLX."""

# Set True while diagnosing startup/freezes to keep board responsive in REPL.
DEBUG_MODE = False

# In debug mode, do not enter deep sleep after each cycle.
ENABLE_LOW_POWER_SLEEP = True

# Delay between loops in debug mode.
DEBUG_LOOP_DELAY_MS = 2000

# Enable display bring-up while keeping other debug safeguards enabled.
ENABLE_EPD = True

# Module generation matters most for command sequence.
EPD_MODULE_VERSION = "V4"

# Waveshare 2.13-inch e-Paper HAT board revision in use.
EPD_REVISION = "2.1"

# Busy polarity for 2.13 V4: 1 means busy, 0 means idle.
EPD_BUSY_ACTIVE_LEVEL = 1

# Rotate display output by 180 degrees to match physical mounting.
EPD_ROTATE_180 = True

# Prefer software SPI first to avoid hardware-SPI constructor differences across ports.
EPD_PREFER_SOFT_SPI = True
EPD_HARD_SPI_BAUD = 4_000_000
EPD_SOFT_SPI_BAUD = 2_000_000

# Wake every 10 minutes.
WAKE_INTERVAL_MS = 10 * 60 * 1000

# Servo pins.
SERVO_PIN_A = 14
SERVO_PIN_B = 15

# E-ink pins (Waveshare 2.13in, SPI0).
EPD_SPI_ID = 0
EPD_PIN_SCK = 18
EPD_PIN_MOSI = 19
EPD_PIN_CS = 17
EPD_PIN_DC = 16
EPD_PIN_RST = 13
EPD_PIN_BUSY = 12

# I2C pins for sensors. Adjust if your wiring changes.
I2C_ID = 1
I2C_PIN_SCL = 27
I2C_PIN_SDA = 26
I2C_FREQ = 400_000

# I2C addresses.
BME280_INTERNAL_ADDR = 0x76
BME280_EXTERNAL_ADDR = 0x77
INA3221_ADDR = 0x40

# INA3221 shunt resistors in ohms for channels 1-3.
INA3221_SHUNTS = (0.1, 0.1, 0.1)
