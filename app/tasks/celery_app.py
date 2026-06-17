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
        "beat_schedule": {
            "dispatch-nightly-commits": {
                "task": "app.tasks.nightly.dispatch_nightly_commits",
                "schedule": crontab(minute="0"),
            },
        },
    })
    return app


celery_app = make_celery()
