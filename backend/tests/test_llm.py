"""LLM interface: Ollama request/response handling (mocked HTTP), fake client, structured output."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.base import ChatMessage, LLMError, LLMOutputError, generate_structured
from app.llm.factory import get_llm_client
from app.llm.fake import FakeLLMClient
from app.llm.ollama import OllamaClient


class Intent(BaseModel):
    intent: str
    confidence: float


def _ollama(handler) -> OllamaClient:  # noqa: ANN001
    return OllamaClient("http://ollama.test", "llama3.1:8b", transport=httpx.MockTransport(handler))


def test_ollama_chat_request_shape_and_parse() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["path"] = request.url.path
        return httpx.Response(
            200, json={"model": "llama3.1:8b", "message": {"role": "assistant", "content": "Hello!"}}
        )

    client = _ollama(handler)
    resp = client.chat([ChatMessage("system", "be nice"), ChatMessage("user", "hi")], temperature=0.2)
    assert resp.text == "Hello!"
    assert resp.model == "llama3.1:8b"
    assert seen["path"] == "/api/chat"
    assert seen["stream"] is False
    assert seen["options"] == {"temperature": 0.2}
    assert seen["messages"] == [{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}]
    assert "format" not in seen


def test_ollama_structured_sends_schema() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"intent": "question", "confidence": 0.9}'}})

    result = generate_structured(_ollama(handler), [ChatMessage("user", "price?")], Intent)
    assert result == Intent(intent="question", confidence=0.9)
    assert seen["format"]["properties"]["intent"]["type"] == "string"
    assert seen["options"]["temperature"] == 0.0


def test_ollama_http_error_raises() -> None:
    client = _ollama(lambda r: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(LLMError, match="404"):
        client.chat([ChatMessage("user", "hi")])


def test_ollama_unreachable_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError, match="not reachable"):
        _ollama(handler).chat([ChatMessage("user", "hi")])


def test_ollama_health_checks_model_pulled() -> None:
    tags = {"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"}]}
    assert _ollama(lambda r: httpx.Response(200, json=tags)).health()
    other = OllamaClient(
        "http://o.test", "gemma2:9b", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tags))
    )
    assert not other.health()


def test_structured_retries_once_then_succeeds() -> None:
    fake = FakeLLMClient()
    fake.enqueue("not json at all", {"intent": "spam", "confidence": 1.0})
    assert generate_structured(fake, [ChatMessage("user", "x")], Intent).intent == "spam"
    assert len(fake.calls) == 2
    # The retry tells the model what went wrong.
    assert "not valid JSON" in fake.calls[1]["messages"][-1].content


def test_structured_gives_up_after_retries() -> None:
    fake = FakeLLMClient()
    fake.enqueue("{}", '{"intent": 5}')
    with pytest.raises(LLMOutputError):
        generate_structured(fake, [ChatMessage("user", "x")], Intent)


def test_fake_default_structured_satisfies_schema() -> None:
    class Nested(BaseModel):
        name: str
        tags: list[str]
        score: int
        flag: bool = True

    class Outer(BaseModel):
        item: Nested
        note: str | None = None

    result = generate_structured(FakeLLMClient(), [ChatMessage("user", "x")], Outer)
    assert result.item.score == 0


def test_fake_plain_echo_and_responder() -> None:
    assert FakeLLMClient().chat([ChatMessage("user", "hi")]).text == "[fake reply] hi"
    fake = FakeLLMClient(responder=lambda msgs, schema: "custom")
    assert fake.chat([ChatMessage("user", "hi")]).text == "custom"


def test_factory_returns_fake_in_tests() -> None:
    get_llm_client.cache_clear()
    assert get_llm_client().name == "fake"
