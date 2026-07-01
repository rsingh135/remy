"""
Structured JSON logging with optional CloudWatch shipping.

Call setup_logging() once at application startup. Every module that uses
`logging.getLogger(__name__)` will automatically emit JSON-formatted records.

A `request_id` field is injected per-request via RequestIdFilter, which reads
from the `request_id` context variable set by the ASGI middleware in main.py.
"""

import contextvars
import logging
import os

from pythonjsonlogger import jsonlogger

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")
        return True


def setup_logging(log_level: str = "INFO") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    _maybe_add_cloudwatch_handler(root, formatter)


def _maybe_add_cloudwatch_handler(root: logging.Logger, formatter: logging.Formatter) -> None:
    log_group = os.getenv("CLOUDWATCH_LOG_GROUP", "")
    if not log_group:
        return

    try:
        import watchtower
        import boto3
        from app.config import get_settings

        s = get_settings()
        cw_client = boto3.client(
            "logs",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
        cw_handler = watchtower.CloudWatchLogHandler(
            boto3_client=cw_client,
            log_group_name=log_group,
            log_stream_name="{strftime:%Y-%m-%d}",
        )
        cw_handler.setFormatter(formatter)
        cw_handler.addFilter(RequestIdFilter())
        root.addHandler(cw_handler)
        root.info("CloudWatch logging enabled → %s", log_group)
    except Exception as exc:
        root.warning("CloudWatch handler setup failed (running without it): %s", exc)
