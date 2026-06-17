from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    # Redis / Celery
    REDIS_URL: str

    # AWS
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str

    # Amazon Bedrock
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    # AWS Pinpoint / End User Messaging
    PINPOINT_APP_ID: str
    AWS_PINPOINT_ORIGINATION_NUMBER: str

    # SNS Security
    SNS_SIGNING_CERT_URL_PREFIX: str = "https://sns.amazonaws.com/"

    # App
    LOG_LEVEL: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
