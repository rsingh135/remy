from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InboundSNSMessage(BaseModel):
    Type: str
    MessageId: str
    TopicArn: str
    Message: str
    Timestamp: datetime
    SignatureVersion: str
    Signature: str
    SigningCertURL: str
    SubscribeURL: Optional[str] = None
    UnsubscribeURL: Optional[str] = None
    Subject: Optional[str] = None


class ParsedSMSBody(BaseModel):
    originationNumber: str
    messageBody: str
    destinationNumber: str
    messageKeyword: Optional[str] = None
