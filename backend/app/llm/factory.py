"""Builds the configured LLM client (LLM_PROVIDER = ollama | fake)."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from app.llm.ollama import OllamaClient


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "fake":
        return FakeLLMClient()
    return OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
