"""Runtime configuration for the invoice automation pipeline.

Credentials for Bedrock come from `aws configure` (~/.aws/credentials).
Model ID / provider settings come from .env (or process environment).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load inv_automate/.env (does not override already-set env vars)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

# ── LLM provider ─────────────────────────────────────────────────────────────
# "bedrock" = AWS Bedrock Converse (Sonnet etc.)
# "ollama"  = local Ollama (e.g. gemma4:e2b)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()

# Bedrock (model id in .env; keys via aws configure)
AWS_REGION = (
    os.getenv("AWS_REGION")
    or os.getenv("aws_region")
    or os.getenv("REGION")
    or "us-east-1"
)
BEDROCK_MODEL_ID = (
    os.getenv("AWS_BEDROCK_INFERENCE_GLOBAL_ID")
    or os.getenv("BEDROCK_MODEL_ARN")
    or ""
).strip()
BEDROCK_READ_TIMEOUT = int(os.getenv("BEDROCK_READ_TIMEOUT", "300"))
BEDROCK_CONNECT_TIMEOUT = int(os.getenv("BEDROCK_CONNECT_TIMEOUT", "10"))
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "8192"))

# Ollama (local fallback)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "16384"))

LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))


def active_model_label() -> str:
    """Human-readable model string for health / UI."""
    if LLM_PROVIDER == "bedrock":
        mid = BEDROCK_MODEL_ID or "(unset — set AWS_BEDROCK_INFERENCE_GLOBAL_ID in .env)"
        # Shorten long ARNs for the badge
        if "claude-sonnet-4-5" in mid:
            return "bedrock:claude-sonnet-4-5"
        if "/" in mid:
            return f"bedrock:{mid.rsplit('/', 1)[-1]}"
        return f"bedrock:{mid}"
    return f"ollama:{OLLAMA_MODEL}"


# ── Extraction / validation ──────────────────────────────────────────────────
ACCURACY_THRESHOLD = float(os.getenv("ACCURACY_THRESHOLD", "0.75"))
MAX_DOCLING_RETRIES = 1
JSON_REPAIR_ATTEMPTS = 3
MAX_PAGES_V1 = int(os.getenv("MAX_PAGES_V1", "15"))

# ── PDF / OCR ────────────────────────────────────────────────────────────────
PDF_DPI = int(os.getenv("PDF_DPI", "200"))
MARKITDOWN_MIN_WORDS = int(os.getenv("MARKITDOWN_MIN_WORDS", "30"))
MARKITDOWN_MIN_UNIQUE_RATIO = float(os.getenv("MARKITDOWN_MIN_UNIQUE_RATIO", "0.3"))

# ── Server ───────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
