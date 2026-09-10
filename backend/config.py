import os
import json
import time
import boto3
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECTS_TABLE: str        = os.getenv("PROJECTS_TABLE",        "pando-projects")
    RESULTS_TABLE: str         = os.getenv("RESULTS_TABLE",         "pando-results")
    JOBS_TABLE: str            = os.getenv("JOBS_TABLE",            "pando-jobs")
    JWT_SECRET_KEY: str        = os.getenv("JWT_SECRET_KEY",        "change-me-in-production")
    AWS_REGION: str            = os.getenv("AWS_REGION",            "ap-south-1")
    AWS_ACCESS_KEY_ID: str     = os.getenv("AWS_ACCESS_KEY_ID",     "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    LOG_GROUP_PREFIX: str      = os.getenv("LOG_GROUP_PREFIX",      "/aws/lambda/invoice-processor")
    ANTHROPIC_API_KEY: str     = os.getenv("ANTHROPIC_API_KEY",     "")
    INTAKE_API_KEY: str        = os.getenv("INTAKE_API_KEY",        "change-me-intake-secret")
    BEDROCK_MODEL_ID: str      = os.getenv("BEDROCK_MODEL_ID",      "us.anthropic.claude-sonnet-4-6")

    # ── Cross-account source (S3 + CloudWatch live in a different AWS account) ──
    # Name of the Secrets Manager secret (in THIS account) holding the other
    # account's IAM user access key/secret key. Leave unset to use this
    # account's own credentials for S3/CloudWatch (single-account setup).
    SOURCE_ACCOUNT_SECRET_NAME: str = os.getenv("SOURCE_ACCOUNT_SECRET_NAME", "")
    # Region the source account's S3 bucket / CloudWatch log group live in.
    # Falls back to AWS_REGION if not set.
    SOURCE_ACCOUNT_REGION: str = os.getenv("SOURCE_ACCOUNT_REGION", "") or AWS_REGION


settings = Settings()


def make_aws_session() -> boto3.Session:
    """
    Return a boto3 Session for THIS account's resources
    (DynamoDB, Bedrock, Secrets Manager, own Lambda invokes).

    When running inside AWS Lambda, AWS_LAMBDA_FUNCTION_NAME is set by the
    runtime and the execution role's credentials are TEMPORARY — they come
    as a matched trio of AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
    AWS_SESSION_TOKEN. Explicitly passing only the first two into
    boto3.Session() (dropping the session token) produces an invalid
    credential set and every API call fails with "The security token
    included in the request is invalid." So inside Lambda we always defer
    to the default credential chain, which correctly picks up all three.

    Locally (outside Lambda), we still honor AWS_ACCESS_KEY_ID /
    AWS_SECRET_ACCESS_KEY from .env if set, falling back to the default
    credential chain (~/.aws/credentials) otherwise.
    """
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return boto3.Session(region_name=settings.AWS_REGION)

    return boto3.Session(
        aws_access_key_id     = settings.AWS_ACCESS_KEY_ID     or None,
        aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY or None,
        aws_session_token     = os.getenv("AWS_SESSION_TOKEN") or None,
        region_name           = settings.AWS_REGION,
    )


# ── Cross-account session (S3 / CloudWatch in another AWS account) ───────────
# Cached at module level so we only call Secrets Manager once per warm Lambda
# container, not on every S3/CloudWatch call.
_source_session_cache: dict = {"session": None, "fetched_at": 0.0}
_SOURCE_SESSION_TTL_SECONDS = 15 * 60  # re-fetch every 15 min in case the secret rotates


def _fetch_source_credentials() -> dict:
    """Fetch {access_key_id, secret_access_key} from Secrets Manager."""
    client = make_aws_session().client("secretsmanager")
    resp = client.get_secret_value(SecretId=settings.SOURCE_ACCOUNT_SECRET_NAME)
    secret = json.loads(resp["SecretString"])
    return {
        "access_key_id":     secret["AWS_ACCESS_KEY_ID"],
        "secret_access_key": secret["AWS_SECRET_ACCESS_KEY"],
        "session_token":     secret.get("AWS_SESSION_TOKEN") or secret.get("SESSION_TOKEN") or "",
    }


def make_source_aws_session() -> boto3.Session:
    """
    Return a boto3 Session for the OTHER AWS account where the invoice
    processor's S3 bucket and CloudWatch log group actually live.

    If SOURCE_ACCOUNT_SECRET_NAME is not configured, falls back to this
    account's own session — i.e. behaves exactly as before for setups where
    everything is in one account.
    """
    if not settings.SOURCE_ACCOUNT_SECRET_NAME:
        return make_aws_session()

    now = time.time()
    if (
        _source_session_cache["session"] is not None
        and now - _source_session_cache["fetched_at"] < _SOURCE_SESSION_TTL_SECONDS
    ):
        return _source_session_cache["session"]

    creds = _fetch_source_credentials()
    session = boto3.Session(
        aws_access_key_id     = creds["access_key_id"],
        aws_secret_access_key = creds["secret_access_key"],
        aws_session_token     = creds.get("session_token") or None,
        region_name           = settings.SOURCE_ACCOUNT_REGION,
    )
    _source_session_cache["session"]    = session
    _source_session_cache["fetched_at"] = now
    return session


def _classify_sts_error(exc: Exception) -> str:
    """Map STS/boto errors to a short status. Never include secret material."""
    code = ""
    msg = str(exc)
    if hasattr(exc, "response") and isinstance(exc.response, dict):
        code = str((exc.response.get("Error") or {}).get("Code") or "")
    combined = f"{code} {msg}".lower()
    if any(tok in combined for tok in ("expiredtoken", "expired token", "request has expired")):
        return "expired"
    if any(tok in combined for tok in (
        "invalidclienttokenid", "invalidaccesskeyid",
        "unrecognizedclient", "the access key id does not exist",
    )):
        return "invalid"
    if "signaturedoesnotmatch" in combined:
        return "bad_secret"
    if "security token" in combined and "invalid" in combined:
        return "expired"
    return "error"


def check_aws_credentials() -> None:
    """
    Print whether local/Lambda AWS creds are usable. Does not print keys.
    STS GetCallerIdentity is the check: valid vs expired vs invalid.
    """
    print("\n" + "─" * 60)
    print("  AWS credentials")
    print("─" * 60)

    in_lambda = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
    access_key = (settings.AWS_ACCESS_KEY_ID or os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    session_token = bool((os.getenv("AWS_SESSION_TOKEN") or "").strip())

    if in_lambda:
        print("  Source          : Lambda execution role")
    elif access_key.startswith("ASIA"):
        print("  Key type        : temporary STS (ASIA) — these expire")
        print(f"  Session token   : {'present' if session_token else 'MISSING (required for ASIA keys)'}")
        if not session_token:
            print("  ✗  Not usable — ASIA keys need AWS_SESSION_TOKEN in backend/.env")
            print("     Refresh keys from the AWS console and paste all three values.")
            print("─" * 60 + "\n")
            return
    elif access_key.startswith("AKIA"):
        print("  Key type        : IAM user (AKIA) — long-lived")
        print(f"  Session token   : {'present' if session_token else 'not set'}")
    elif access_key:
        print("  Key type        : unrecognized prefix")
    else:
        print("  Key type        : default credential chain (~/.aws or instance role)")

    try:
        ident = make_aws_session().client("sts").get_caller_identity()
        arn = ident.get("Arn") or ""
        print("  STS             : valid")
        if arn:
            print(f"  Identity        : {arn}")
        print("  ✓  Credentials are active (DynamoDB / Bedrock / this account).")
        print("     Invoice PDFs still need S3 permission on the Pando bucket.")
    except Exception as exc:
        status = _classify_sts_error(exc)
        if status == "expired":
            print("  STS             : EXPIRED")
            print("  ✗  Refresh AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,")
            print("     and AWS_SESSION_TOKEN in backend/.env, then restart.")
        elif status == "invalid":
            print("  STS             : INVALID (key id does not exist)")
            print("  ✗  Paste a current access key from the AWS console.")
        elif status == "bad_secret":
            print("  STS             : SECRET MISMATCH")
            print("  ✗  Secret key does not match the access key id.")
        else:
            print(f"  STS             : FAILED ({status})")
            print(f"     {type(exc).__name__}: {exc}")
        if access_key.startswith("ASIA"):
            print("     Temporary keys expire in hours — all three values must be replaced together.")

    print("─" * 60 + "\n")
