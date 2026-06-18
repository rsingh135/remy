import asyncio

import boto3

from app.config import get_settings

_eum_client = None


def _get_eum_client():
    global _eum_client
    if _eum_client is None:
        s = get_settings()
        _eum_client = boto3.client(
            "pinpoint-sms-voice-v2",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
    return _eum_client


def _send_sms_boto3(phone_number: str, message: str) -> None:
    s = get_settings()
    client = _get_eum_client()
    client.send_text_message(
        DestinationPhoneNumber=phone_number,
        OriginationIdentity=s.EUM_ORIGINATION_IDENTITY,
        MessageBody=message[:160],
        MessageType="TRANSACTIONAL",
    )


async def send_sms(phone_number: str, message: str) -> None:
    await asyncio.to_thread(_send_sms_boto3, phone_number, message)
