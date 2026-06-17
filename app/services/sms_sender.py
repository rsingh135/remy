import asyncio

import boto3

from app.config import get_settings

_pinpoint_client = None


def _get_pinpoint_client():
    global _pinpoint_client
    if _pinpoint_client is None:
        s = get_settings()
        _pinpoint_client = boto3.client(
            "pinpoint",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
    return _pinpoint_client


def _send_sms_boto3(phone_number: str, message: str) -> None:
    s = get_settings()
    client = _get_pinpoint_client()
    client.send_messages(
        ApplicationId=s.PINPOINT_APP_ID,
        MessageRequest={
            "Addresses": {phone_number: {"ChannelType": "SMS"}},
            "MessageConfiguration": {
                "SMSMessage": {
                    "Body": message[:160],
                    "MessageType": "TRANSACTIONAL",
                    "OriginationNumber": s.AWS_PINPOINT_ORIGINATION_NUMBER,
                }
            },
        },
    )


async def send_sms(phone_number: str, message: str) -> None:
    await asyncio.to_thread(_send_sms_boto3, phone_number, message)
