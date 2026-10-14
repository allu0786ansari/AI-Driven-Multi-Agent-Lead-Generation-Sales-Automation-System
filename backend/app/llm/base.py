"""LLM interface used by every agent.

Agents never call Ollama (or later a hosted API) directly; they call an
`LLMClient`. Swapping Ollama for Claude/GPT later means adding one new client
class, nothing else (proposal Section 6).

Structured output: `generate_structured()` asks the model for JSON matching a
Pydantic model, validates it, retries once, and raises `LLMOutputError` if the
model still returns something invalid - the caller then hands off to a human
(spec Section 10).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    """The LLM could not be reached or returned an error."""


class LLMOutputError(LLMError):
    """The LLM answered, but not in the required structure."""


class LLMClient(Protocol):
    name: str

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...

    def health(self) -> bool: ...


def generate_structured(
    client: LLMClient,
    messages: list[ChatMessage],
    model_cls: type[T],
    *,
    retries: int = 1,
) -> T:
    """Ask for JSON matching `model_cls`; validate; retry on invalid output."""
    schema = model_cls.model_json_schema()
    attempt_messages = list(messages)
    last_error = ""
    for attempt in range(retries + 1):
        response = client.chat(attempt_messages, json_schema=schema, temperature=0.0)
        try:
            return model_cls.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:500]
            log.warning(
                "LLM returned invalid structured output",
                extra={"attempt": attempt + 1, "model": response.model},
            )
            attempt_messages = [
                *messages,
                ChatMessage("assistant", response.text),
                ChatMessage(
                    "user",
                    "Your previous answer was not valid JSON for the required schema. "
                    "Reply again with ONLY a JSON object that matches the schema.",
                ),
            ]
    raise LLMOutputError(f"Invalid structured output after {retries + 1} attempts: {last_error}")
