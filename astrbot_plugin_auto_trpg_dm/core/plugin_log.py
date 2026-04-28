from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "auto_trpg_dm.private"


def configure_plugin_logging(
    log_path: Path,
    max_bytes: int = 2_000_000,
    backup_count: int = 5,
) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_logger = logging.getLogger(LOGGER_NAME)
    plugin_logger.setLevel(logging.INFO)
    plugin_logger.propagate = False

    for handler in list(plugin_logger.handlers):
        plugin_logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    plugin_logger.addHandler(handler)
    plugin_logger.info("plugin_logger_configured path=%s max_bytes=%s backups=%s", log_path, max_bytes, backup_count)
    return plugin_logger


def get_plugin_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
