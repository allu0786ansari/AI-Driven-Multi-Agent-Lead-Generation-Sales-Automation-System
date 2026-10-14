"""Embedding providers for product search.

- HashingEmbeddings: no model download, deterministic. Word + character
  trigram features hashed into a fixed-size vector. Good at spelling variants
  and transliteration ("fryer" / "frayer"), weak at meaning. Default so the
  system works on any machine, and used in tests.
- OllamaEmbeddings: a real local embedding model (e.g. bge-m3, which is
  multilingual). Choose with scripts/eval_retrieval.py.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from typing import Protocol

import httpx

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    @property
    def model_id(self) -> str:
        """Identifies the model; stored with each vector so a model switch triggers re-indexing."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(normalize_text(text))


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    """Vectors are stored L2-normalized, so the dot product is the cosine."""
    return sum(x * y for x, y in zip(a, b, strict=False))


class HashingEmbeddings:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    @property
    def model_id(self) -> str:
        return f"hashing-v1-{self.dim}"

    def _slot(self, feature: str) -> tuple[int, float]:
        h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def _features(self, text: str) -> Counter[str]:
        feats: Counter[str] = Counter()
        for word in tokenize(text):
            feats[f"w:{word}"] += 2
            padded = f"#{word}#"
            for i in range(len(padded) - 2):
                feats[f"c:{padded[i : i + 3]}"] += 1
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feature, count in self._features(text).items():
                idx, sign = self._slot(feature)
                vec[idx] += sign * (1.0 + math.log(count))
            out.append(_l2(vec))
        return out


class OllamaEmbeddings:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        batch_size: int = 32,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport)

    @property
    def model_id(self) -> str:
        return f"ollama:{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                resp = self._http.post("/api/embed", json={"model": self.model, "input": batch})
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"Ollama not reachable: {exc}") from exc
            if resp.status_code != 200:
                raise EmbeddingError(f"Ollama embed error {resp.status_code}: {resp.text[:300]}")
            embeddings = resp.json().get("embeddings") or []
            if len(embeddings) != len(batch):
                raise EmbeddingError("Ollama returned a different number of embeddings than inputs")
            vectors.extend(_l2([float(v) for v in e]) for e in embeddings)
        return vectors
