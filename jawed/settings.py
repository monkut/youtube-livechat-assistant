import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# logging.basicConfig set in __init__.py
logger = logging.getLogger()

DEFAULT_LOG_LEVEL = "DEBUG"
LOG_LEVEL = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
if LOG_LEVEL and LOG_LEVEL in ("INFO", "ERROR", "WARNING", "DEBUG", "CRITICAL"):
    level = getattr(logging, LOG_LEVEL)
    logger.setLevel(level)
DEFAULT_REGION = "ap-northeast-1"
REGION = os.getenv("REGION", DEFAULT_REGION)
