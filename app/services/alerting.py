"""
Admin alerting via SNS.

`send_admin_alert` is the single call site for all ops alerts (task failures,
queue backup, cost guardrail hits). It posts to an SNS topic whose ARN is set
via ADMIN_ALERT_SNS_TOPIC_ARN. If that env var is empty the alert is logged at
ERROR level instead — so local dev and test environments get visibility without
needing a real SNS topic.
"""

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)

_sns_client = None


def _get_sns_client():
    global _sns_client
    if _sns_client is None:
        s = get_settings()
        _sns_client = boto3.client(
            "sns",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
    return _sns_client


def send_admin_alert(subject: str, message: str) -> None:
    """
    Publish an ops alert to the configured SNS topic.

    Falls back to a structured ERROR log if ADMIN_ALERT_SNS_TOPIC_ARN is unset
    so this is always safe to call in any environment.
    """
    s = get_settings()
    topic_arn = s.ADMIN_ALERT_SNS_TOPIC_ARN

    if not topic_arn:
        logger.error("[ALERT] %s — %s", subject, message)
        return

    try:
        _get_sns_client().publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=message,
        )
        logger.info("Admin alert sent: %s", subject)
    except (BotoCoreError, ClientError) as exc:
        logger.error("Failed to publish admin alert '%s': %s", subject, exc)
