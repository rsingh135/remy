import base64
import re

import httpx
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.x509 import load_pem_x509_certificate
from fastapi import HTTPException

from app.config import get_settings
from app.schemas.sms import InboundSNSMessage

_cert_cache: dict[str, bytes] = {}

_NOTIFICATION_FIELDS = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
_SUBSCRIPTION_FIELDS = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]


def _build_canonical_string(envelope: InboundSNSMessage, raw: dict) -> bytes:
    if envelope.Type == "SubscriptionConfirmation":
        fields = _SUBSCRIPTION_FIELDS
    else:
        fields = _NOTIFICATION_FIELDS

    parts = []
    for field in fields:
        value = raw.get(field)
        if value is not None:
            parts.append(f"{field}\n{value}\n")
    return "".join(parts).encode("utf-8")


async def verify_sns_signature(envelope: InboundSNSMessage, raw: dict | None = None) -> None:
    settings = get_settings()

    if not envelope.SigningCertURL.startswith(settings.SNS_SIGNING_CERT_URL_PREFIX):
        raise HTTPException(status_code=403, detail="Invalid SNS signing cert URL")

    if not re.match(r"^https://sns\.[a-z0-9-]+\.amazonaws\.com/", envelope.SigningCertURL):
        raise HTTPException(status_code=403, detail="Invalid SNS signing cert URL format")

    if envelope.SigningCertURL not in _cert_cache:
        async with httpx.AsyncClient() as client:
            resp = await client.get(envelope.SigningCertURL)
            resp.raise_for_status()
            _cert_cache[envelope.SigningCertURL] = resp.content

    pem_bytes = _cert_cache[envelope.SigningCertURL]
    cert = load_pem_x509_certificate(pem_bytes)
    sig_bytes = base64.b64decode(envelope.Signature)

    if raw is None:
        raw = envelope.model_dump()

    canonical = _build_canonical_string(envelope, raw)

    try:
        cert.public_key().verify(sig_bytes, canonical, PKCS1v15(), SHA1())
    except Exception:
        raise HTTPException(status_code=403, detail="SNS signature verification failed")
