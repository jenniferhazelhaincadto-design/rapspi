from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "app.db"

SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 9600
SERIAL_SIMULATE = False

VOICE_MODEL = "small"
VOICE_COMPUTE_TYPE = "int8"
