"""
QdrantMemory — memoria vettoriale a lungo termine basata su Qdrant.

API compatibile con SemanticMemory (add / search / delete) in modo che il resto
del codice non debba cambiare.

Storage: Qdrant locale su http://localhost:6333
Dashboard: http://localhost:6333/dashboard (zoom + click sui punti)
Embedding: tramite EmbeddingClient (Ollama o OpenAI-compatibile)

La collection viene creata automaticamente al primo accesso se non esiste.
Payload per ogni punto:
  content        : testo originale
  created_at     : ISO 8601
  last_access    : ISO 8601
  access_count   : int
  wing           : dominio di alto livello (es. "progetto:foo", "utente")
  hall           : categoria (es. "preferences", "events", "facts")
  room           : argomento specifico (opzionale)
  metadata       : dict arbitrario
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.memory.embedding_client import EmbeddingClient


class QdrantMemory:
    def __init__(
        self,
        host: str,
        port: int,
        collection: str,
        vector_size: int,
        embedder: "EmbeddingClient",
        user_id: str = "ltsia",
    ):
        self.host = host
        self.port = port
        self.collection = collection
        self.vector_size = vector_size
        self.embedder = embedder
        self.user_id = user_id
        self._client = None
        self._ready = False
        self._init_client()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_client(self) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError:
            self._client = None
            self._ready = False
            return

        try:
            self._client = QdrantClient(host=self.host, port=self.port, timeout=10)
            if not self._client.collection_exists(self.collection):
                self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )
            self._ready = True
        except Exception:
            self._client = None
            self._ready = False

    def is_ready(self) -> bool:
        return self._ready and self._client is not None

    def ping(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    # ── Public API (compat SemanticMemory) ───────────────────────────────────

    def add(self, content: str, metadata: Optional[dict] = None) -> Optional[str]:
        """Aggiunge una voce alla memoria. Ritorna l'id o None se fallisce."""
        if not self.is_ready():
            return None
        embedding = self.embedder.embed(content)
        if embedding is None:
            return None

        from qdrant_client.models import PointStruct

        md = dict(metadata or {})
        now = datetime.now().isoformat()
        payload = {
            "content": content,
            "user_id": self.user_id,
            "created_at": now,
            "last_access": now,
            "access_count": 0,
            "wing": md.pop("wing", "default"),
            "hall": md.pop("hall", "facts"),
            "room": md.pop("room", ""),
            "metadata": md,
        }

        mem_id = str(uuid.uuid4())
        try:
            self._client.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=mem_id, vector=embedding, payload=payload)],
            )
        except Exception:
            return None
        return mem_id

    def search(self, query: str, limit: int = 5, threshold: float = 0.0) -> list[dict]:
        """Ricerca semantica. Ritorna [{id, content, score, payload}]."""
        if not self.is_ready():
            return []
        query_emb = self.embedder.embed(query)
        if query_emb is None:
            return []

        try:
            hits = self._client.query_points(
                collection_name=self.collection,
                query=query_emb,
                limit=limit,
                score_threshold=threshold if threshold > 0 else None,
            ).points
        except Exception:
            return []

        results: list[dict] = []
        ids_to_touch: list[str] = []
        for h in hits:
            payload = h.payload or {}
            results.append({
                "id": str(h.id),
                "content": payload.get("content", ""),
                "score": float(h.score) if h.score is not None else 0.0,
                "payload": payload,
            })
            ids_to_touch.append(str(h.id))

        if ids_to_touch:
            self._touch(ids_to_touch)
        return results

    def delete(self, memory_id: str) -> bool:
        if not self.is_ready():
            return False
        try:
            from qdrant_client.models import PointIdsList
            self._client.delete(
                collection_name=self.collection,
                points_selector=PointIdsList(points=[memory_id]),
            )
            return True
        except Exception:
            return False

    def count(self) -> int:
        if not self.is_ready():
            return 0
        try:
            return self._client.count(collection_name=self.collection, exact=True).count
        except Exception:
            return 0

    # ── Internals ─────────────────────────────────────────────────────────────

    def _touch(self, ids: list[str]) -> None:
        """Aggiorna last_access e incrementa access_count per i punti restituiti."""
        if not self._client:
            return
        try:
            now = datetime.now().isoformat()
            points = self._client.retrieve(
                collection_name=self.collection,
                ids=ids,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                payload["last_access"] = now
                payload["access_count"] = int(payload.get("access_count", 0)) + 1
                self._client.set_payload(
                    collection_name=self.collection,
                    payload=payload,
                    points=[p.id],
                )
        except Exception:
            pass
