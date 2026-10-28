import json
import logging
from datetime import datetime, timezone

from prometheus_client import Counter, Histogram

REQUESTS = Counter(
    "url_shortener_http_requests_total",
    "HTTP requests served by the URL shortener.",
    ("method", "route", "status"),
)
REQUEST_DURATION = Histogram(
    "url_shortener_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "processed_events",
            "pending_events",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("url_shortener")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
