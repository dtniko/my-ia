"""
ContextAgent — costruisce e arricchisce il contesto iniziale di ogni sessione.

Flusso:
  1. All'avvio, riceve un hint sulla sessione (o stringa vuota).
  2. Cerca nella SemanticMemory le memorie più rilevanti.
  3. Include tutte le memorie permanenti (istruzioni cross-sessione).
  4. Restituisce un blocco di testo pronto per il system prompt.
  5. Controlla il budget token e lancia alert se si avvicina al limite.
  6. Permette di arricchire la memoria semantica con nuove informazioni.

Budget consigliato:
  - WARN  : 6.000 token (circa 24.000 chars)
  - MAX   : 8.000 token (circa 32.000 chars)
  Su 128k token totali questo lascia >118k per la conversazione.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from src.ui.cli import CLI

if TYPE_CHECKING:
    from src.memory.core_facts import CoreFactsMemory
    from src.memory.semantic_memory import SemanticMemory
    from src.memory.qdrant_memory import QdrantMemory


# ── Token budget ──────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4  # stima conservativa


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SessionContext:
    """Risultato di build_session_context."""
    text: str                      # testo pronto per il system prompt
    estimated_tokens: int          # stima token
    semantic_hits: list[dict] = field(default_factory=list)  # memorie trovate
    permanent_hits: list[dict] = field(default_factory=list) # memorie permanenti
    alert_level: str = "ok"        # "ok" | "warn" | "max"
    alert_message: str = ""


# ── ContextAgent ──────────────────────────────────────────────────────────────

class ContextAgent:
    """
    Gestisce il contesto iniziale di sessione con memoria semantica.

    Parametri
    ---------
    permanent_memory  : PermanentMemory      — memorie JSON cross-sessione
    semantic_memory   : SemanticMemory|None  — memoria vettoriale (opzionale)
    warn_tokens       : int                  — soglia gialla (default 6.000)
    max_tokens        : int                  — soglia rossa  (default 8.000)
    semantic_results  : int                  — quante memorie semantiche recuperare
    semantic_threshold: float                — score minimo cosine similarity
    """

    def __init__(
        self,
        permanent_memory: Optional["CoreFactsMemory"] = None,
        semantic_memory: Optional["SemanticMemory"] = None,
        qdrant_memory: Optional["QdrantMemory"] = None,
        warn_tokens: int = 6000,
        max_tokens: int = 8000,
        semantic_results: int = 10,
        semantic_threshold: float = 0.35,
    ):
        self.permanent_memory = permanent_memory
        self.semantic_memory = semantic_memory
        self.qdrant_memory = qdrant_memory
        self.warn_tokens = warn_tokens
        self.max_tokens = max_tokens
        self.semantic_results = semantic_results
        self.semantic_threshold = semantic_threshold

    # ── Pubblica API ──────────────────────────────────────────────────────────

    def build_session_context(self, session_hint: str = "") -> SessionContext:
        """
        Costruisce il contesto iniziale per questa sessione.

        session_hint — stringa opzionale che descrive l'argomento/obiettivo
                       della sessione. Se vuota usa "LTSIA sessione generale".
        """
        query = session_hint.strip() or "LTSIA sessione generale"

        # 1. Memorie semantiche rilevanti: preferenza Qdrant, fallback semantic_memory legacy
        semantic_hits: list[dict] = []
        active_semantic = (
            self.qdrant_memory if (self.qdrant_memory and self.qdrant_memory.is_ready())
            else self.semantic_memory
        )
        if active_semantic:
            try:
                semantic_hits = active_semantic.search(
                    query,
                    limit=self.semantic_results,
                    threshold=self.semantic_threshold,
                )
            except Exception as e:
                CLI.warning(f"ContextAgent: ricerca semantica fallita: {e}")

        # 2. Fatti core (sempre inclusi)
        permanent_hits = self.permanent_memory.get_all() if self.permanent_memory else []

        # 3. Assembla testo contesto
        text = self._assemble(semantic_hits, permanent_hits)

        # 4. Controlla budget token
        tokens = _estimate_tokens(text)
        alert_level, alert_message = self._check_budget(tokens)

        # 5. Stampa alert a video
        if alert_level == "warn":
            CLI.warning(f"[ContextAgent] {alert_message}")
        elif alert_level == "max":
            CLI.error(f"[ContextAgent] {alert_message}")
            # Tronca il contesto semantico per stare nel limite
            text = self._truncate_to_budget(semantic_hits, permanent_hits)
            tokens = _estimate_tokens(text)
            alert_message += f" — contesto semantico troncato ({tokens} token)"

        return SessionContext(
            text=text,
            estimated_tokens=tokens,
            semantic_hits=semantic_hits,
            permanent_hits=permanent_hits,
            alert_level=alert_level,
            alert_message=alert_message,
        )

    def enrich(self, content: str, metadata: Optional[dict] = None) -> bool:
        """
        Aggiunge una nuova informazione alla memoria semantica.
        Ritorna True se salvata, False se semantic memory non disponibile o errore.
        """
        if not self.semantic_memory:
            return False
        try:
            mem_id = self.semantic_memory.add(content, metadata or {})
            return mem_id is not None
        except Exception as e:
            CLI.warning(f"ContextAgent: arricchimento memoria fallito: {e}")
            return False

    def check_context_budget(self, context_text: str) -> tuple[str, str]:
        """
        Controlla il budget token di un testo di contesto.
        Ritorna (alert_level, message): alert_level in {"ok", "warn", "max"}.
        """
        tokens = _estimate_tokens(context_text)
        return self._check_budget(tokens)

    # ── Privati ───────────────────────────────────────────────────────────────

    def _assemble(
        self,
        semantic_hits: list[dict],
        permanent_hits: list[dict],
    ) -> str:
        parts: list[str] = []

        if semantic_hits:
            parts.append("## Contesto sessione — memorie rilevanti\n")
            for hit in semantic_hits:
                score_pct = int(hit.get("score", 0) * 100)
                parts.append(f"- [{score_pct}%] {hit['content']}")

        if permanent_hits:
            parts.append("\n## Istruzioni e memorie permanenti\n")
            for entry in permanent_hits:
                tag = entry.get("type", "info")
                parts.append(f"- [{tag}] {entry['content']}")

        return "\n".join(parts)

    def _check_budget(self, tokens: int) -> tuple[str, str]:
        if tokens >= self.max_tokens:
            return (
                "max",
                f"Contesto iniziale TROPPO GRANDE: ~{tokens} token "
                f"(limite {self.max_tokens}). Libera memorie o riduci il contesto.",
            )
        if tokens >= self.warn_tokens:
            return (
                "warn",
                f"Contesto iniziale grande: ~{tokens} token "
                f"(soglia warning {self.warn_tokens}/{self.max_tokens}). "
                "Considera di rimuovere memorie obsolete.",
            )
        return "ok", ""

    def _truncate_to_budget(
        self,
        semantic_hits: list[dict],
        permanent_hits: list[dict],
    ) -> str:
        """Riduci le memorie semantiche finché il testo sta nel budget max."""
        hits = list(semantic_hits)
        while hits:
            candidate = self._assemble(hits, permanent_hits)
            if _estimate_tokens(candidate) <= self.max_tokens:
                return candidate
            hits = hits[:-1]  # rimuovi la meno rilevante (in fondo, score più basso)
        # Solo permanenti
        return self._assemble([], permanent_hits)
