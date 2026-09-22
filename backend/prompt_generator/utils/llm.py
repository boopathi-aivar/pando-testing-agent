"""LLM client — Bedrock (boto3 Converse) or Ollama, selected via LLM_PROVIDER.

For Bedrock, credentials are sourced from the same Secrets Manager secret
used by the observability DynamoDB session (make_observability_ddb_session),
since Bedrock lives in the same AWS account as those tables.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Sequence, Union

from prompt_generator.config import (
    BEDROCK_CONNECT_TIMEOUT,
    BEDROCK_MAX_TOKENS,
    BEDROCK_MODEL_ID,
    BEDROCK_READ_TIMEOUT,
    LLM_NUM_CTX,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    active_model_label,
)

logger = logging.getLogger(__name__)

MessageTuple = tuple[str, str]


def _make_bedrock_client(region: str):
    """
    Build a bedrock-runtime boto3 client using credentials from the same
    Secrets Manager secret that observability DynamoDB uses — Bedrock lives
    in the same account as those tables.
    """
    from botocore.config import Config
    from config import make_observability_ddb_session  # main backend config.py

    session = make_observability_ddb_session()
    return session.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            read_timeout=BEDROCK_READ_TIMEOUT,
            connect_timeout=BEDROCK_CONNECT_TIMEOUT,
        ),
    )


class BedrockChat:
    """Minimal ChatOllama-compatible wrapper over bedrock-runtime converse."""

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> None:
        if not model_id:
            raise ValueError(
                "Bedrock model id missing. Set AWS_BEDROCK_INFERENCE_GLOBAL_ID in .env"
            )

        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self._client = _make_bedrock_client(region)
        logger.info(
            "Bedrock client ready — model=%s region=%s json_mode=%s",
            model_id,
            region,
            json_mode,
        )

    def invoke(self, messages: Sequence[Union[MessageTuple, Any]]) -> SimpleNamespace:
        system_parts: list[str] = []
        converse_messages: list[dict] = []

        for item in messages:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                role, content = item[0], item[1]
            else:
                role = getattr(item, "type", None) or getattr(item, "role", "user")
                content = getattr(item, "content", str(item))

            role_l = str(role).lower()
            text = str(content)
            if role_l in ("system", "developer"):
                system_parts.append(text)
            elif role_l in ("human", "user"):
                converse_messages.append({"role": "user", "content": [{"text": text}]})
            elif role_l in ("ai", "assistant"):
                converse_messages.append({"role": "assistant", "content": [{"text": text}]})
            else:
                converse_messages.append({"role": "user", "content": [{"text": text}]})

        if self.json_mode:
            system_parts.append(
                "Respond with valid JSON only. No markdown fences or commentary."
            )

        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages or [{"role": "user", "content": [{"text": ""}]}],
            "inferenceConfig": {
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if system_parts:
            kwargs["system"] = [{"text": "\n\n".join(system_parts)}]

        response = self._client.converse(**kwargs)
        text = (
            response.get("output", {})
            .get("message", {})
            .get("content", [{}])[0]
            .get("text", "")
            or ""
        )
        return SimpleNamespace(content=text.strip())


@lru_cache(maxsize=4)
def get_llm(*, json_mode: bool = False):
    """Return a chat client with `.invoke(messages)` (Bedrock or Ollama)."""
    provider = LLM_PROVIDER
    logger.info("LLM provider=%s model=%s", provider, active_model_label())

    if provider == "bedrock":
        # Region comes from the observability account config (same account as Bedrock)
        from config import settings as main_settings  # main backend config.py
        bedrock_region = main_settings.OBSERVABILITY_DDB_REGION or "us-east-1"
        return BedrockChat(
            model_id=BEDROCK_MODEL_ID,
            region=bedrock_region,
            temperature=LLM_TEMPERATURE,
            max_tokens=BEDROCK_MAX_TOKENS,
            json_mode=json_mode,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs: dict[str, Any] = {
            "model": OLLAMA_MODEL,
            "base_url": OLLAMA_BASE_URL,
            "temperature": LLM_TEMPERATURE,
            "num_ctx": LLM_NUM_CTX,
        }
        if json_mode:
            kwargs["format"] = "json"
        return ChatOllama(**kwargs)

    raise ValueError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'bedrock' or 'ollama'."
    )
