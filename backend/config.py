import os
import boto3
from dotenv import load_dotenv

load_dotenv()


class Settings:
    MONGODB_URL: str           = os.getenv("MONGODB_URL",           "mongodb://localhost:27017")
    MONGODB_DB: str            = os.getenv("MONGODB_DB",            "pando_testing_agent")
    JWT_SECRET_KEY: str        = os.getenv("JWT_SECRET_KEY",        "change-me-in-production")
    AWS_REGION: str            = os.getenv("AWS_REGION",            "us-east-1")
    AWS_ACCESS_KEY_ID: str     = os.getenv("AWS_ACCESS_KEY_ID",     "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    LOG_GROUP_PREFIX: str      = os.getenv("LOG_GROUP_PREFIX",      "/aws/lambda/invoice-processor")
    ANTHROPIC_API_KEY: str     = os.getenv("ANTHROPIC_API_KEY",     "")
    INTAKE_API_KEY: str        = os.getenv("INTAKE_API_KEY",        "change-me-intake-secret")
    # Bedrock model — override via BEDROCK_MODEL_ID env var if needed
    BEDROCK_MODEL_ID: str      = os.getenv("BEDROCK_MODEL_ID",      "us.anthropic.claude-sonnet-4-6")


settings = Settings()


def make_aws_session() -> boto3.Session:
    """
    Return a boto3 Session using credentials from .env.
    Falls back to the default credential chain (IAM role, ~/.aws/credentials)
    if the env vars are not set.
    """
    return boto3.Session(
        aws_access_key_id     = settings.AWS_ACCESS_KEY_ID     or None,
        aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY or None,
        region_name           = settings.AWS_REGION,
    )
