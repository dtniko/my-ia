"""
SemanticMemory — memoria semantica locale con SQLite + cosine similarity.

Storage: ~/.ltsia/semantic_memory.db
Embedding: EmbeddingClient (Ollama o vLLM)
Ricerca: cosine similarity in Python (O(n), ottimale per <10k voci)

Schema:
  id         TEXT PRIMARY KEY  — UUID v4
  user_id    TEXT              — namespace
  content    TEXT              — testo originale
  metadata   TEXT              — JSON
  embedding  TEXT              — JSON array di float
  created_at TEXT              — ISO 8601
"""
from __future__ import annotations
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.embedding_client import EmbeddingClient


class SemanticMemory:
    def __init__(
        self,
        db_path: str,
        user_id: str,
        embedder: "EmbeddingClient",
    ):
        self.db_path  = db_path
        self.user_id  = user_id
        self.embedder = embedder
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, content: str, metadata: Optional[dict] = None) -> Optional[str]:
        """Aggiunge una voce alla memoria. Ritorna l'id o None se embedding fallisce."""
        embedding = self.embedder.embed(content)
        if embedding is None:
            return None

        mem_id = str(uuid.uuid4())
        self._db().execute(
            """INSERT INTO memories (id, user_id, content, metadata, embedding, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                mem_id,
                self.user_id,
                content,
                json.dumps(metadata or {}),
                json.dumps(embedding),
                datetime.now().isoformat(),
            ),
        )
        self._db().commit()
        return mem_id

    def search(self, query: str, limit: int = 5, threshold: float = 0.0) -> list[dict]:
        """Cerca le voci più simili a `query` per significato."""
        query_emb = self.embedder.embed(query)
        if query_emb is None:
            return []

        rows = self._db().execute(
            "SELECT id, content, embedding FROM memories WHERE user_id = ?",
            (self.user_id,),
        ).fetchall()

        scored = []
        for row_id, content, emb_json in rows:
            try:
                emb = json.loads(emb_json)
            except Exception:
                continue
            score = _cosine_similarity(query_emb, emb)
            if score >= threshold:
                scored.append({"id": row_id, "content": content, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def delete(self, memory_id: str) -> bool:
        cur = self._db().execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, self.user_id),
        )
        self._db().commit()
        return cur.rowcount > 0

    # ── Internals ─────────────────────────────────────────────────────────────

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._db()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                content    TEXT NOT NULL,
                metadata   TEXT DEFAULT '{}',
                embedding  TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)"
        )
        conn.commit()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._conn


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot  = 0.0
    mag_a = 0.0
    mag_b = 0.0
    length = min(len(a), len(b))
    for i in range(length):
        dot   += a[i] * b[i]
        mag_a += a[i] * a[i]
        mag_b += b[i] * b[i]
    denom = math.sqrt(mag_a) * math.sqrt(mag_b)
    if denom < 1e-10:
        return 0.0
    return dot / denom
