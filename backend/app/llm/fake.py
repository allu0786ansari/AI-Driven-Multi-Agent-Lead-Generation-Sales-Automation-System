"""Deterministic fake LLM for tests and for running without Ollama.

Responses come from, in order:
1. a queue of scripted responses (`enqueue`), if any are left;
2. a responder function, if given;
3. a default: for structured requests, the minimal JSON that satisfies the
   schema's required fields; otherwise an echo of the last user message.

Every call is recorded in `calls`, so tests can assert what the agent asked.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from typing import Any

from app.llm.base import ChatMessage, LLMResponse

Responder = Callable[[list[ChatMessage], dict[str, Any] | None], str]


class FakeLLMClient:
    name = "fake"

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder
        self._queue: deque[str] = deque()
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, *responses: str | dict[str, Any]) -> None:
        for r in responses:
            self._queue.append(r if isinstance(r, str) else json.dumps(r))

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            {"messages": list(messages), "json_schema": json_schema, "temperature": temperature}
        )
        if self._queue:
            text = self._queue.popleft()
        elif self.responder is not None:
            text = self.responder(messages, json_schema)
        elif json_schema is not None:
            text = json.dumps(_minimal_instance(json_schema, json_schema))
        else:
            last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
            text = f"[fake reply] {last_user}"
        return LLMResponse(text=text, model="fake", latency_ms=0)

    def health(self) -> bool:
        return True

    @property
    def is_scripted(self) -> bool:
        """False when nothing was scripted - i.e. there is no real model behind this client."""
        return bool(self._queue) or self.responder is not None


def _minimal_instance(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        return _minimal_instance(root.get("$defs", {}).get(ref, {}), root)
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return _minimal_instance(schema["anyOf"][0], root)
    if "default" in schema:
        return schema["default"]
    match schema.get("type"):
        case "object":
            props = schema.get("properties", {})
            return {k: _minimal_instance(props[k], root) for k in schema.get("required", []) if k in props}
        case "array":
            return []
        case "string":
            return ""
        case "integer":
            return int(schema.get("minimum", 0))
        case "number":
            return float(schema.get("minimum", 0))
        case "boolean":
            return False
        case "null":
            return None
    return None
