import asyncio
import logging

from app.services.sms_sender import _send_sms_boto3
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.reminders.send_reminder",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_reminder(self, phone_number: str, message: str) -> None:
    try:
        _send_sms_boto3(phone_number, message)
        logger.info("Reminder sent to %s", phone_number)
    except Exception as exc:
        logger.error("Failed to send reminder to %s: %s", phone_number, exc)
        raise self.retry(exc=exc)
