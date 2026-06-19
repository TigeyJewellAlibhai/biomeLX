"""Hardware and firmware configuration for BiomeLX."""

# Set True while diagnosing startup/freezes to keep board responsive in REPL.
DEBUG_MODE = False

# In debug mode, do not enter deep sleep after each cycle.
ENABLE_LOW_POWER_SLEEP = True

# Delay between loops in debug mode.
DEBUG_LOOP_DELAY_MS = 20_000

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

# Some panel/controller combos need bit order reversed per byte.
EPD_REVERSE_BITS = False
EPD_CLEAR_ON_INIT = False
EPD_ENABLE_QUICK_REFRESH = False

# Prefer software SPI first to avoid hardware-SPI constructor differences across ports.
EPD_PREFER_SOFT_SPI = True
EPD_HARD_SPI_BAUD = 4_000_000
EPD_SOFT_SPI_BAUD = 2_000_000

# Wake every 10 minutes.
WAKE_INTERVAL_MS = 10 * 60 * 1000
STANDARD_WAKE_INTERVAL_MS = 10 * 60 * 1000
POWER_SAVER_WAKE_INTERVAL_MS = 60 * 60 * 1000

# Button handling.
BUTTON_MODE_PIN = 8
BUTTON_SERVO_PIN = 9
BUTTON_DEBOUNCE_MS = 250
BUTTON_ACTION_DISPLAY_MS = 5_000
AWAKE_IDLE_TIMEOUT_MS = 60_000
ENABLE_BUTTON_WAKE = True
SLEEP_POLL_MS = 1_000

# Servo positions.
SERVO_CLOSED_ANGLE = 220
SERVO_OPEN_ANGLE = 0
# Motion mode: "ramped" (default) or "simple" (direct set_angle).
SERVO_MOTION_MODE = "ramped"
SERVO_MOVE_TOTAL_MS = 8_000
SERVO_MOVE_STEP_MS = 35
# 0.0 = linear, 1.0 = strongest ease-in/ease-out ramp.
SERVO_RAMP_STRENGTH = 0.12
# Minimum delta per update step to avoid tiny stall-prone increments.
SERVO_MIN_STEP_DEG = 1.5
# Initial push to break static friction on heavy linkages.
SERVO_BREAKAWAY_DEG = 6.0
SERVO_BREAKAWAY_HOLD_MS = 120
SERVO_PWM_FREQ = 50
SERVO_MIN_PULSE_US = 1000
SERVO_MAX_PULSE_US = 2000
SERVO_SPAN_DEG = 270

# Servo pins.
SERVO_PIN_A = 14
SERVO_PIN_B = 15
# Persist canopy state so boot restores last known position.
SERVO_STATE_FILE = "canopy_state.txt"
# Used only if state file is missing or unreadable.
SERVO_DEFAULT_OPEN = False
# Automatic daily open/close schedule (local time, HH:MM).
CANOPY_SCHEDULE_ENABLED = False
CANOPY_OPEN_TIME_HM = "07:00"
CANOPY_CLOSE_TIME_HM = "20:00"
# Optional rain override: close canopy whenever rain percentage is high.
CANOPY_RAIN_OVERRIDE_ENABLED = False
CANOPY_RAIN_CLOSE_PCT = 70

# E-ink pins (Waveshare 2.13in, SPI0).
EPD_SPI_ID = 0
EPD_PIN_SCK = 18
EPD_PIN_MOSI = 19
EPD_PIN_CS = 17
EPD_PIN_DC = 16
EPD_PIN_RST = 13
EPD_PIN_BUSY = 12

# BME280 sensors on I2C0: SDA=GP0, SCL=GP1.
BME_I2C_ID = 0
BME_I2C_PIN_SCL = 1
BME_I2C_PIN_SDA = 0
BME_I2C_FREQ = 50_000
BME_PREFER_SOFT_I2C = True

# INA3221 bus (old wiring): I2C0, SDA=GP4, SCL=GP5.
INA_I2C_ID = 0
INA_I2C_PIN_SCL = 5
INA_I2C_PIN_SDA = 4
INA_I2C_FREQ = 50_000
INA_PREFER_SOFT_I2C = True

# Backward-compatible aliases for older code paths.
I2C_ID = BME_I2C_ID
I2C_PIN_SCL = BME_I2C_PIN_SCL
I2C_PIN_SDA = BME_I2C_PIN_SDA
I2C_FREQ = BME_I2C_FREQ

# I2C addresses.
BME280_INTERNAL_ADDR = 0x77
BME280_EXTERNAL_ADDR = 0x76
INA3221_ADDR = 0x40

# INA3221 shunt resistors in ohms for channels 1-3.
INA3221_SHUNTS = (0.1, 0.1, 0.1)
INA_BATT_CHANNEL = 1
INA_SOLAR_CHANNEL = 2
INA_BATT_VOLTAGE_SCALE = 1.0
INA_SOLAR_VOLTAGE_SCALE = 1.0

# Weather fetch (Open-Meteo) over Wi-Fi.
ENABLE_WEATHER = True
WIFI_SSID = "LordOfThePings-2.4GHz"
WIFI_PASSWORD = "Theeaglesarecoming"
WIFI_CONNECT_TIMEOUT_MS = 30_000

# AP fallback used when station connection fails in wireless mode.
WIFI_AP_SSID = "BiomeLX-Setup"
WIFI_AP_PASSWORD = ""

# Embedded web UI controls.
WEB_UI_PORT = 80
WEB_UI_ALT_PORT = 8080
WEB_UI_DEBUG = True
WEB_UI_CLIENT_TIMEOUT_S = 1.5

# Time synchronization (NTP) while online.
ENABLE_WEB_TIME_SYNC = True
NTP_HOST = "pool.ntp.org"
# Local offset from UTC in hours. Example: -4 for EDT, -5 for EST.
TIMEZONE_OFFSET_HOURS = -4
TIME_SYNC_NTP_RETRIES = 3
TIME_HTTP_FALLBACK_ENABLED = True
TIME_HTTP_FALLBACK_URL = "http://worldtimeapi.org/api/timezone/Etc/UTC"
TIME_SYNC_MIN_VALID_YEAR = 2024

# CSV logging.
LOG_ENABLED = True
LOG_DIR = "logs"
LOG_MAX_FILES = 4
LOG_FILE_PREFIX = "biomelx"

# Set your location for weather query.
WEATHER_LATITUDE = 42.35
WEATHER_LONGITUDE = -71.16

# Forecast look-ahead window used for rain-soon indicator.
WEATHER_RAIN_SOON_HOURS = 3
# Weather API endpoint and reliability knobs for constrained network stacks.
WEATHER_API_BASE_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ALLOW_HTTP_FALLBACK = True
WIFI_PULL_CONNECT_RETRIES = 2
WIFI_PULL_RESET_RADIO = True

# Sensor sampling: collect readings for this duration on wake, then average.
SENSOR_SAMPLE_WINDOW_MS = 1_000
SENSOR_SAMPLE_PERIOD_MS = 100
