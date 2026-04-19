"""
EmbeddingClient — genera vettori di embedding via Ollama o API OpenAI-compatibile.

Modelli consigliati per Ollama:
  nomic-embed-text     (768 dim, veloce, ottimo per italiano)
  mxbai-embed-large    (1024 dim, più preciso)
  all-minilm           (384 dim, molto veloce)
"""
from __future__ import annotations
import json
from typing import Optional

import requests


class EmbeddingClient:
    def __init__(
        self,
        host: str,
        port: int,
        model: str,
        api_type: str = "ollama",   # "ollama" | "openai"
        timeout: int = 30,
    ):
        self.base_url = f"http://{host}:{port}"
        self.model    = model
        self.api_type = api_type
        self.timeout  = timeout

    def embed(self, text: str) -> Optional[list[float]]:
        """Genera l'embedding per `text`. Ritorna None in caso di errore."""
        try:
            if self.api_type == "openai":
                return self._embed_openai(text)
            return self._embed_ollama(text)
        except Exception:
            return None

    # ── Ollama (/api/embed) ───────────────────────────────────────────────────

    def _embed_ollama(self, text: str) -> Optional[list[float]]:
        resp = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # Ollama v0.3+ restituisce {"embeddings": [[...]]}
        if "embeddings" in data:
            emb = data["embeddings"]
            if emb and isinstance(emb[0], list):
                return emb[0]
            return emb

        # Formato legacy: {"embedding": [...]}
        if "embedding" in data:
            return data["embedding"]

        return None

    # ── OpenAI-compatible (/v1/embeddings) ───────────────────────────────────

    def _embed_openai(self, text: str) -> Optional[list[float]]:
        resp = requests.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return r.status_code < 500
        except Exception:
            return False
