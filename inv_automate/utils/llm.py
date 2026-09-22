"""LLM client — Bedrock (boto3 Converse) or Ollama, selected via LLM_PROVIDER."""

from __future__ import annotations

import logging
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Sequence, Union

from config import (
    AWS_REGION,
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
        import boto3
        from botocore.config import Config

        if not model_id:
            raise ValueError(
                "Bedrock model id missing. Set AWS_BEDROCK_INFERENCE_GLOBAL_ID "
                "(or BEDROCK_MODEL_ARN) in inv_automate/.env"
            )

        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                read_timeout=BEDROCK_READ_TIMEOUT,
                connect_timeout=BEDROCK_CONNECT_TIMEOUT,
            ),
        )
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
                converse_messages.append(
                    {"role": "user", "content": [{"text": text}]}
                )
            elif role_l in ("ai", "assistant"):
                converse_messages.append(
                    {"role": "assistant", "content": [{"text": text}]}
                )
            else:
                converse_messages.append(
                    {"role": "user", "content": [{"text": text}]}
                )

        if self.json_mode:
            system_parts.append(
                "Respond with valid JSON only. No markdown fences or commentary."
            )

        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages
            or [{"role": "user", "content": [{"text": ""}]}],
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
        return BedrockChat(
            model_id=BEDROCK_MODEL_ID,
            region=AWS_REGION,
            temperature=LLM_TEMPERATURE,
            max_tokens=BEDROCK_MAX_TOKENS,
            json_mode=json_mode,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs = {
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
