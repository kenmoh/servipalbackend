import logging.config
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(name: str = "servipal"):
    """Configure application logging"""

    # Create base log directory
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create log file path
    log_file = log_dir / "cron_jobs.log"

    # Create logger instance
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Check if logger already has handlers to avoid duplicates
    if not logger.handlers:
        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_format)
        logger.addHandler(console_handler)

        # File Handler
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=1024 * 1024,  # 1MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


def configure_production_logging():
    """Configure production logging settings"""

    # Create logs directory if it doesn't exist
    log_dir = Path("/var/log/servipal")
    log_dir.mkdir(parents=True, exist_ok=True)

    # Define logging configuration
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - [%(process)d] - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "/var/log/servipal/app.log",
                "maxBytes": 1024 * 1024 * 10,  # 10MB
                "backupCount": 5,
                "formatter": "verbose",
                "level": "INFO",
            },
            "cron": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "/var/log/servipal/cron.log",
                "maxBytes": 1024 * 1024 * 10,  # 10MB
                "backupCount": 5,
                "formatter": "verbose",
                "level": "INFO",
            },
            "error": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "/var/log/servipal/error.log",
                "maxBytes": 1024 * 1024 * 10,  # 10MB
                "backupCount": 5,
                "formatter": "verbose",
                "level": "ERROR",
            },
        },
        "loggers": {
            "servipal": {
                "handlers": ["file", "error"],
                "level": "INFO",
                "propagate": True,
            },
            "servipal.cron": {
                "handlers": ["cron", "error"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    # Apply configuration
    logging.config.dictConfig(logging_config)
