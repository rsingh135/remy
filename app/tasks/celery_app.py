from celery import Celery
from celery.schedules import crontab

from app.config import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "remy",
        broker=settings.REDIS_URL,
        backend=settings.REDIS_URL,
        include=["app.tasks.nightly", "app.tasks.reminders"],
    )
    app.config_from_object({
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
        "broker_connection_retry_on_startup": True,
        # Reliability: ack only after the task completes, requeue if the worker dies.
        "task_acks_late": True,
        "task_reject_on_worker_lost": True,
        # Prevent workers from hoarding ETA tasks ahead of their scheduled time.
        "worker_prefetch_multiplier": 1,
        # Must exceed the longest ETA delay (24 h covers all practical reminders).
        # Without this, Redis re-delivers ETA tasks before they are due.
        "broker_transport_options": {"visibility_timeout": 86400},
        "beat_schedule": {
            "dispatch-nightly-commits": {
                "task": "app.tasks.nightly.dispatch_nightly_commits",
                "schedule": crontab(minute="0"),
            },
        },
    })
    return app


celery_app = make_celery()
