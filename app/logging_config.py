"""
Production logging helpers.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class ExactLevelFilter(logging.Filter):
    """Allow only records for a single logging level."""

    def __init__(self, level: int):
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self.level


def _normalize_log_dir(app) -> Path:
    configured = Path(app.config.get('LOG_DIR') or 'logs')
    if configured.is_absolute():
        return configured
    return (Path(app.root_path).parent / configured).resolve()


def _handler_exists(logger: logging.Logger, target_path: Path) -> bool:
    target = str(target_path)
    return any(getattr(handler, 'baseFilename', None) == target for handler in logger.handlers)


def _build_rotating_handler(
    path: Path,
    level: int,
    formatter: logging.Formatter,
    *,
    max_bytes: int,
    backup_count: int,
    exact_level: int | None = None,
) -> RotatingFileHandler:
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if exact_level is not None:
        handler.addFilter(ExactLevelFilter(exact_level))
    return handler


def configure_logging(app) -> None:
    """Attach rotating application, access, warning, and error logs."""
    if app.config.get('TESTING'):
        return

    log_dir = _normalize_log_dir(app)
    log_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = int(app.config.get('LOG_MAX_BYTES') or (10 * 1024 * 1024))
    backup_count = int(app.config.get('LOG_BACKUP_COUNT') or 10)
    log_level = str(app.config.get('LOG_LEVEL') or 'INFO').upper()
    log_to_stdout = bool(app.config.get('LOG_TO_STDOUT', True))

    app_formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )
    access_formatter = logging.Formatter('%(asctime)s %(message)s')

    app_logger = app.logger
    app_logger.setLevel(log_level)

    log_files = {
        'app': log_dir / 'app.log',
        'warning': log_dir / 'warning.log',
        'error': log_dir / 'error.log',
        'access': log_dir / 'access.log',
        'security': log_dir / 'security.log',
    }

    if not _handler_exists(app_logger, log_files['app']):
        app_logger.addHandler(
            _build_rotating_handler(
                log_files['app'],
                logging.INFO,
                app_formatter,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        )
    if not _handler_exists(app_logger, log_files['warning']):
        app_logger.addHandler(
            _build_rotating_handler(
                log_files['warning'],
                logging.WARNING,
                app_formatter,
                max_bytes=max_bytes,
                backup_count=backup_count,
                exact_level=logging.WARNING,
            )
        )
    if not _handler_exists(app_logger, log_files['error']):
        app_logger.addHandler(
            _build_rotating_handler(
                log_files['error'],
                logging.ERROR,
                app_formatter,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        )

    access_logger = logging.getLogger('retroquest.access')
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    if not _handler_exists(access_logger, log_files['access']):
        access_logger.addHandler(
            _build_rotating_handler(
                log_files['access'],
                logging.INFO,
                access_formatter,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        )

    security_logger = logging.getLogger('retroquest.security')
    security_logger.setLevel(logging.INFO)
    security_logger.propagate = False
    if not _handler_exists(security_logger, log_files['security']):
        security_logger.addHandler(
            _build_rotating_handler(
                log_files['security'],
                logging.INFO,
                app_formatter,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        )

    if log_to_stdout and not any(
        isinstance(handler, logging.StreamHandler) and not getattr(handler, 'baseFilename', None)
        for handler in app_logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(log_level)
        stream_handler.setFormatter(app_formatter)
        app_logger.addHandler(stream_handler)

    app.extensions['access_logger'] = access_logger
    app.extensions['security_logger'] = security_logger
