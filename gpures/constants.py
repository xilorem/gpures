import os
from datetime import timedelta
from pathlib import Path

DEFAULT_DATA_DIR = Path(os.environ.get("GPURES_HOME", "/var/lib/gpures"))
DEFAULT_DB_NAME = "reservations.sqlite"
DEFAULT_MAX_ADVANCE = timedelta(days=7)
TIME_FMT = "%Y-%m-%d %H:%M"
ISO_FMT = "%Y-%m-%dT%H:%M:%S%z"
