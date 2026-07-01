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
    BEDROCK_MODEL_ID: str = "us.anthropic.claude-sonnet-4-6"
    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    # AWS End User Messaging SMS
    EUM_ORIGINATION_IDENTITY: str  # Phone number ARN or E.164 number from EUM console

    # SNS Security
    SNS_SIGNING_CERT_URL_PREFIX: str = "https://sns.amazonaws.com/"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/sms/auth/google/callback"

    # Public base URL (used to build auth links sent via SMS)
    BASE_URL: str = "http://localhost:8000"

    # Photon iMessage (alternative to AWS EUM for testing; set PHOTON_ENABLED=true to activate)
    PHOTON_ENABLED: bool = False
    PHOTON_PROJECT_ID: str = ""
    PHOTON_PROJECT_SECRET: str = ""
    PHOTON_WEBHOOK_SECRET: str = ""

    # App
    LOG_LEVEL: str = "INFO"
    DEV_SKIP_SNS_VERIFY: bool = False  # Set true in tests to bypass signature check

    # Observability
    CLOUDWATCH_LOG_GROUP: str = ""  # e.g. "remy-api" — empty disables CloudWatch shipping
    ADMIN_ALERT_SNS_TOPIC_ARN: str = ""  # SNS topic for ops alerts — empty disables
    QUEUE_DEPTH_ALERT_THRESHOLD: int = 50  # Celery queue length that triggers an alert

    # Bedrock cost guardrails (tokens per user per day)
    BEDROCK_DAILY_TOKEN_SOFT_CAP: int = 50000   # nudge Claude to be brief above this
    BEDROCK_DAILY_TOKEN_HARD_CAP: int = 100000  # block Bedrock calls above this

    # REST API auth (iOS app)
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24 * 30  # 30-day tokens
    OTP_TTL_SECONDS: int = 600  # 10 minutes


@lru_cache
def get_settings() -> Settings:
    return Settings()
