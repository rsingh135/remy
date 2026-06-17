import asyncio
import logging

from app.services.sms_sender import _send_sms_boto3
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.reminders.send_reminder")
def send_reminder(phone_number: str, message: str) -> None:
    try:
        _send_sms_boto3(phone_number, message)
        logger.info("Reminder sent to %s", phone_number)
    except Exception as e:
        logger.error("Failed to send reminder to %s: %s", phone_number, e)
        raise
