"""Ollama client (local LLM). Uses /api/chat with `format` set to a JSON schema
for structured output (supported by Ollama 0.5 and later)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMError, LLMResponse


class OllamaClient:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport)

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_schema is not None:
            body["format"] = json_schema

        started = time.perf_counter()
        try:
            resp = self._http.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama not reachable at {self._http.base_url}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = (data.get("message") or {}).get("content", "")
        return LLMResponse(text=text, model=data.get("model", self.model), latency_ms=latency_ms, raw=data)

    def health(self) -> bool:
        """True if Ollama is running and the configured model is pulled."""
        try:
            resp = self._http.get("/api/tags")
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        names = {m.get("name") for m in resp.json().get("models", [])}
        return self.model in names or f"{self.model}:latest" in names

    def close(self) -> None:
        self._http.close()
