import asyncio
import hashlib
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _send_message(phone_number: str, message: str) -> None:
    """Send via Photon if enabled, else fall back to AWS EUM SMS."""
    from app.config import get_settings
    s = get_settings()
    if s.PHOTON_ENABLED:
        from app.services.photon_sender import send_via_photon
        asyncio.run(send_via_photon(phone_number, message))
    else:
        from app.services.sms_sender import _send_sms_boto3
        _send_sms_boto3(phone_number, message)


@celery_app.task(
    name="app.tasks.reminders.send_reminder",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_reminder(self, phone_number: str, message: str) -> None:
    import redis as redis_lib
    from app.config import get_settings

    r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
    dedup_key = f"reminder_sent:{self.request.id}"

    # Atomic SET NX: acquire before sending so a retry after a crash never re-sends.
    acquired = r.set(dedup_key, "1", nx=True, ex=86400)
    if not acquired:
        logger.info("Reminder %s already delivered, skipping", self.request.id)
        return

    # Content-based dedup: catches duplicate tasks (different IDs, same recipient+message)
    # scheduled within a 5-minute window — e.g. if add_reminder fired twice upstream.
    content_hash = hashlib.md5(f"{phone_number}:{message}".encode()).hexdigest()
    content_key = f"reminder_content:{content_hash}"
    content_acquired = r.set(content_key, self.request.id, nx=True, ex=300)
    if not content_acquired:
        logger.warning(
            "Reminder content duplicate suppressed for %s (task %s)", phone_number, self.request.id
        )
        return

    try:
        _send_message(phone_number, message)
        logger.info("Reminder sent to %s", phone_number)
    except Exception as exc:
        # Release both keys so the retry can re-acquire and attempt again.
        r.delete(dedup_key)
        r.delete(content_key)
        logger.error("Failed to send reminder to %s: %s", phone_number, exc)
        raise self.retry(exc=exc)
