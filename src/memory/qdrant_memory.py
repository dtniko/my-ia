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
        dedup_threshold: float = 0.93,
    ):
        self.host = host
        self.port = port
        self.collection = collection
        self.vector_size = vector_size
        self.embedder = embedder
        self.user_id = user_id
        self.dedup_threshold = dedup_threshold
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

    def add(self, content: str, metadata: Optional[dict] = None, skip_dedup: bool = False) -> Optional[str]:
        """Aggiunge una voce alla memoria. Ritorna l'id o None se fallisce.

        Dedup pre-insert: se esiste già un punto con similarità >= dedup_threshold
        non viene inserito un nuovo punto — incrementa access_count sull'esistente
        e ritorna il suo id.
        """
        if not self.is_ready():
            return None
        embedding = self.embedder.embed(content)
        if embedding is None:
            return None

        if not skip_dedup and self.dedup_threshold > 0:
            existing_id = self._find_duplicate(embedding)
            if existing_id:
                self._reinforce(existing_id)
                return existing_id

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

    # ── Helper per MemoryOptimizerAgent ───────────────────────────────────────

    def scroll(self, limit: int = 50, offset=None) -> tuple[list[dict], object]:
        """Scorre la collection a blocchi. Ritorna (points, next_offset)."""
        if not self.is_ready():
            return [], None
        try:
            result = self._client.scroll(
                collection_name=self.collection,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            points, next_offset = result if isinstance(result, tuple) else (result.points, result.next_page_offset)
            out = []
            for p in points:
                vec = p.vector
                if isinstance(vec, dict):
                    vec = next(iter(vec.values()), None)
                out.append({
                    "id": str(p.id),
                    "vector": vec,
                    "payload": dict(p.payload or {}),
                })
            return out, next_offset
        except Exception:
            return [], None

    def find_similar(self, vector: list[float], limit: int = 4, exclude_id: Optional[str] = None) -> list[dict]:
        """Ritorna i vicini del vettore (esclude `exclude_id` se fornito)."""
        if not self.is_ready() or vector is None:
            return []
        try:
            hits = self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=limit,
            ).points
        except Exception:
            return []
        out = []
        for h in hits:
            hid = str(h.id)
            if exclude_id and hid == exclude_id:
                continue
            out.append({
                "id": hid,
                "content": (h.payload or {}).get("content", ""),
                "score": float(h.score) if h.score is not None else 0.0,
                "payload": dict(h.payload or {}),
            })
        return out

    def get(self, memory_id: str) -> Optional[dict]:
        """Recupera un punto per id (payload + vector)."""
        if not self.is_ready():
            return None
        try:
            pts = self._client.retrieve(
                collection_name=self.collection,
                ids=[memory_id],
                with_payload=True,
                with_vectors=True,
            )
            if not pts:
                return None
            p = pts[0]
            vec = p.vector
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            return {"id": str(p.id), "vector": vec, "payload": dict(p.payload or {})}
        except Exception:
            return None

    def upsert_raw(self, content: str, payload_overrides: Optional[dict] = None) -> Optional[str]:
        """Inserisce un punto con payload controllato (usato per merge/split: salta dedup).

        Ri-embed il content; ritorna il nuovo id.
        """
        if not self.is_ready():
            return None
        embedding = self.embedder.embed(content)
        if embedding is None:
            return None
        from qdrant_client.models import PointStruct

        over = dict(payload_overrides or {})
        now = datetime.now().isoformat()
        payload = {
            "content": content,
            "user_id": self.user_id,
            "created_at": over.pop("created_at", now),
            "last_access": over.pop("last_access", now),
            "access_count": int(over.pop("access_count", 0)),
            "wing": over.pop("wing", "default"),
            "hall": over.pop("hall", "facts"),
            "room": over.pop("room", ""),
            "metadata": over.pop("metadata", {}),
        }
        payload.update(over)

        mem_id = str(uuid.uuid4())
        try:
            self._client.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=mem_id, vector=embedding, payload=payload)],
            )
        except Exception:
            return None
        return mem_id

    # ── Internals ─────────────────────────────────────────────────────────────

    def _find_duplicate(self, embedding: list[float]) -> Optional[str]:
        """Cerca il punto più simile; se sopra soglia ritorna il suo id."""
        try:
            hits = self._client.query_points(
                collection_name=self.collection,
                query=embedding,
                limit=1,
                score_threshold=self.dedup_threshold,
            ).points
        except Exception:
            return None
        if not hits:
            return None
        return str(hits[0].id)

    def _reinforce(self, memory_id: str) -> None:
        """Incrementa access_count e aggiorna last_access di un punto esistente."""
        if not self._client:
            return
        try:
            pts = self._client.retrieve(
                collection_name=self.collection,
                ids=[memory_id],
                with_payload=True,
                with_vectors=False,
            )
            if not pts:
                return
            payload = dict(pts[0].payload or {})
            payload["last_access"] = datetime.now().isoformat()
            payload["access_count"] = int(payload.get("access_count", 0)) + 1
            self._client.set_payload(
                collection_name=self.collection,
                payload=payload,
                points=[pts[0].id],
            )
        except Exception:
            pass

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
