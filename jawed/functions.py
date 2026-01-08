import logging
from pathlib import Path  # noqa: TC003

logger = logging.getLogger(__name__)


def process(filepath: Path, output_directory: Path) -> None:
    logger.debug(f"filepath={filepath}")
    logger.debug(f"output_directory={output_directory}")
