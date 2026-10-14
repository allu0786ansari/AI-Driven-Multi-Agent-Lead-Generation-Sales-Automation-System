"""Check that the configured LLM works, including structured (JSON) output.

    python -m scripts.llm_smoke

With LLM_PROVIDER=ollama this needs Ollama running and the model pulled
(`ollama pull <OLLAMA_MODEL>`).
"""

from __future__ import annotations

import sys
import time

from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm.base import ChatMessage, LLMError, generate_structured
from app.llm.factory import get_llm_client


class IntentCheck(BaseModel):
    intent: str = Field(description="one of: purchase_interest, question, complaint, spam, other")
    product_mentioned: str | None = None


def main() -> int:
    settings = get_settings()
    client = get_llm_client()
    print(f"Provider: {settings.llm_provider}  Model: {getattr(client, 'model', '-')}")
    if not client.health():
        print(
            "LLM not ready. Start Ollama and run:  ollama pull " + settings.ollama_model,
            file=sys.stderr,
        )
        return 1

    try:
        t = time.perf_counter()
        reply = client.chat(
            [ChatMessage("user", "Reply with one short friendly sentence greeting a customer.")]
        )
        print(f"Plain chat ({(time.perf_counter() - t):.1f}s): {reply.text.strip()}")

        t = time.perf_counter()
        result = generate_structured(
            client,
            [
                ChatMessage(
                    "system",
                    "Classify the customer's comment. intent must be one of: "
                    "purchase_interest, question, complaint, spam, other.",
                ),
                ChatMessage("user", "Bhai air fryer ka price kya hai? Delivery Chennai mein hoga?"),
            ],
            IntentCheck,
        )
        print(f"Structured ({(time.perf_counter() - t):.1f}s): {result.model_dump()}")
    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
