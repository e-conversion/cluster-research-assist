"""JSON-lines logging for the ``cra`` logger tree.

Every record is one line ``{ts, level, logger, msg, ...fields, exc?}``, where
``fields`` is whatever the caller passes as ``extra={"fields": {...}}``. The
same file is read back by the stats endpoint, which is why the format is
machine-readable rather than pretty.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = "cra"
LOG_FILE = "app.log"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 3


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            line.update(fields)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(
    log_dir: Path | None, level: int = logging.INFO
) -> logging.Logger:
    """Attach stderr and, when ``log_dir`` is given, a rotating file handler.

    Calling it again replaces the handlers, so tests can redirect the file.
    """
    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    formatter = JsonFormatter()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    root.addHandler(stderr)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    root.setLevel(level)
    root.propagate = False
    return root
