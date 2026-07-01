"""
Operational monitoring tasks.

`check_queue_depth` runs every 15 minutes via Celery Beat. If the default
Celery queue length exceeds the configured threshold, it fires an SNS admin alert.
"""

import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.monitoring.check_queue_depth")
def check_queue_depth() -> None:
    import redis as redis_lib
    from app.config import get_settings
    from app.services.alerting import send_admin_alert

    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)
    depth = r.llen("celery")

    logger.info("Celery queue depth: %d", depth)

    if depth > s.QUEUE_DEPTH_ALERT_THRESHOLD:
        send_admin_alert(
            subject=f"[Remy] Celery queue backup: {depth} tasks",
            message=(
                f"The default Celery queue has {depth} pending tasks, "
                f"exceeding the threshold of {s.QUEUE_DEPTH_ALERT_THRESHOLD}. "
                "Workers may be under-provisioned or stuck."
            ),
        )
