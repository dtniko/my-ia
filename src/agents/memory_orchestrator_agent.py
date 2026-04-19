"""
MemoryOrchestratorAgent — arricchisce ogni richiesta utente con contesto
tiered (short → medium → long) + fallback web automatico.

Evolve il vecchio ContextAgent: oltre a costruire il contesto iniziale di
sessione, viene chiamato PRE-TURN per ogni messaggio dell'utente.

Flusso per ogni richiesta:
  1. Cerca in short-term (scope corrente).
  2. Cerca in medium-term (FTS5).
  3. Cerca in long-term via MemorySearcherAgent (ASMR parallel search).
  4. Se confidence < soglia → invoca web_fallback (SearchAgent).
  5. Se l'utente ha chiesto esplicitamente "cerca online" → web forzato.
  6. Assembla un blocco di contesto formato pronto da iniettare come
     messaggio di sistema pre-turn.

A fine turno, il MemoryReaderAgent può estrarre fatti e persisterli nei
livelli appropriati (chiamato dal ChatAgent in callback).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.memory.permanent_memory import PermanentMemory
from src.ui.cli import CLI


WEB_INTENT_PATTERNS = [
    r"\bcerca (online|in rete|su internet|nel web)\b",
    r"\bsearch (online|the web|internet)\b",
    r"\btrova online\b",
    r"\bgoogle\b",
    r"\bricerca web\b",
]


@dataclass
class EnrichedContext:
    """Risultato di enrich_request()."""
    text: str                               # blocco pronto per il system/pre-turn
    hits_short: list[dict] = field(default_factory=list)
    hits_medium: list[dict] = field(default_factory=list)
    hits_long: list[dict] = field(default_factory=list)
    pinned_directives: list[dict] = field(default_factory=list)
    web_result: str = ""
    confidence: float = 0.0
    used_web: bool = False
    used_web_forced: bool = False

    def has_content(self) -> bool:
        return bool(
            self.hits_short
            or self.hits_medium
            or self.hits_long
            or self.web_result
            or self.pinned_directives
        )


class MemoryOrchestratorAgent:
    def __init__(
        self,
        permanent_memory: PermanentMemory,
        short_term=None,
        medium_term=None,
        long_term=None,
        searcher=None,              # MemorySearcherAgent
        reader=None,                # MemoryReaderAgent
        web_search_delegate: Optional[Callable[[str], str]] = None,
        web_fallback_threshold: float = 0.5,
        max_hits_per_tier: int = 5,
    ):
        self.permanent_memory = permanent_memory
        self.short = short_term
        self.medium = medium_term
        self.long = long_term
        self.searcher = searcher
        self.reader = reader
        self.web_search = web_search_delegate
        self.web_fallback_threshold = web_fallback_threshold
        self.max_hits_per_tier = max_hits_per_tier

    # ── Contesto iniziale di sessione (compat ContextAgent) ───────────────────

    def build_session_context(self, session_hint: str = "") -> str:
        """Contesto statico a inizio sessione: memorie permanenti + sommari medium."""
        parts = []

        # Permanent memory (istruzioni cross-sessione)
        perm = self.permanent_memory.get_all() if self.permanent_memory else []
        if perm:
            parts.append("## Istruzioni e memorie permanenti")
            for e in perm:
                tag = e.get("type", "info")
                parts.append(f"- [{tag}] {e.get('content', '')}")
            parts.append("")

        # Ultimi eventi salienti dalla medium-term
        if self.medium:
            try:
                stats = self.medium.stats()
                if stats.get("rooms", 0) > 0:
                    parts.append(
                        f"## Memoria medio termine — {stats['wings']} wing, "
                        f"{stats['rooms']} room, {stats['drawers']} drawer"
                    )
            except Exception:
                pass

        # Long-term availability
        if self.long and getattr(self.long, "is_ready", lambda: False)():
            try:
                n = self.long.count()
                parts.append(f"## Memoria lungo termine (Qdrant): {n} voci")
            except Exception:
                pass

        return "\n".join(parts)

    # ── Pre-turn enrichment ───────────────────────────────────────────────────

    def enrich_request(self, user_msg: str, scope: str = "session") -> EnrichedContext:
        """
        Arricchisce la richiesta utente con contesto tiered + (opzionale) web.
        Da chiamare PRIMA di inoltrare il messaggio al ChatAgent.
        """
        ctx = EnrichedContext(text="")

        if not user_msg.strip():
            return ctx

        # 0: pinned directives dalla PermanentMemory (sempre incluse, senza ricerca)
        if self.permanent_memory:
            try:
                for e in self.permanent_memory.get_all():
                    ctx.pinned_directives.append({
                        "type": e.get("type", "instruction"),
                        "content": e.get("content", ""),
                    })
            except Exception:
                pass

        # 1-3: searcher parallelo short+medium+long
        if self.searcher:
            try:
                result = self.searcher.search(user_msg, scope=scope, limit=self.max_hits_per_tier * 3)
                ctx.confidence = result.get("confidence", 0.0)
                for h in result.get("hits", []):
                    tier = h.get("tier", "long")
                    if tier == "short":
                        ctx.hits_short.append(h)
                    elif tier == "medium":
                        ctx.hits_medium.append(h)
                    else:
                        ctx.hits_long.append(h)
            except Exception as e:
                CLI.warning(f"MemoryOrchestrator: searcher fallito: {e}")

        # 4. Web fallback forzato se richiesta esplicita
        user_wants_web = _user_asked_web(user_msg)
        if user_wants_web and self.web_search:
            ctx.used_web_forced = True
            ctx.web_result = self._safe_web(user_msg)
        # 5. Web fallback automatico se confidence bassa
        elif self.web_search and ctx.confidence < self.web_fallback_threshold:
            has_any_hits = bool(ctx.hits_short or ctx.hits_medium or ctx.hits_long)
            # evita il fallback se non c'è nulla ma la query è conversazionale/non informativa
            if not has_any_hits and _looks_like_lookup(user_msg):
                ctx.used_web = True
                ctx.web_result = self._safe_web(user_msg)

        ctx.text = self._assemble(ctx)
        return ctx

    def _safe_web(self, query: str) -> str:
        try:
            return self.web_search(query) or ""
        except Exception as e:
            CLI.warning(f"MemoryOrchestrator: web search fallita: {e}")
            return ""

    def _assemble(self, ctx: EnrichedContext) -> str:
        parts: list[str] = []

        if ctx.pinned_directives:
            parts.append("### Istruzioni permanenti (da rispettare SEMPRE)")
            for d in ctx.pinned_directives:
                parts.append(f"- [{d.get('type','info')}] {d.get('content','')}")
            parts.append("")

        if ctx.hits_short:
            parts.append("### Memoria breve (sessione corrente)")
            for h in ctx.hits_short[: self.max_hits_per_tier]:
                role = h.get("role", "?")
                parts.append(f"- [{role}] {h.get('content','')[:200]}")
            parts.append("")

        if ctx.hits_medium:
            parts.append("### Memoria media (MemPalace)")
            for h in ctx.hits_medium[: self.max_hits_per_tier]:
                loc = f"{h.get('wing','?')}/{h.get('hall','?')}/{h.get('room','?')}"
                parts.append(f"- [{loc}] {h.get('content','')[:200]}")
            parts.append("")

        if ctx.hits_long:
            parts.append("### Memoria lunga (Qdrant)")
            for h in ctx.hits_long[: self.max_hits_per_tier]:
                payload = h.get("payload") or {}
                loc = f"{payload.get('wing','?')}/{payload.get('hall','?')}"
                score = h.get("score", 0.0)
                parts.append(f"- [{loc}] ({score*100:.0f}%) {h.get('content','')[:200]}")
            parts.append("")

        if ctx.web_result:
            tag = "web (richiesta esplicita)" if ctx.used_web_forced else "web (fallback)"
            parts.append(f"### Risultati {tag}")
            parts.append(ctx.web_result[:3000])
            parts.append("")

        if not parts:
            return ""
        header = "## Contesto recuperato per questa richiesta"
        return header + "\n\n" + "\n".join(parts)

    # ── Post-turn ingestion ───────────────────────────────────────────────────

    def ingest_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        scope: str = "session",
    ) -> dict:
        """
        A fine turno: scrive i drawer in short-term, invoca il reader per estrarre
        fatti e li scrive in medium/long.

        Ritorna un dict con quello che è stato salvato.
        """
        written = {"short_drawers": 0, "medium_rooms": 0, "long_points": 0}

        if self.short:
            try:
                s = self.short.scope(scope)
                if user_msg:
                    s.add_drawer("user", user_msg)
                    written["short_drawers"] += 1
                if assistant_msg:
                    s.add_drawer("assistant", assistant_msg)
                    written["short_drawers"] += 1
            except Exception:
                pass

        if not self.reader:
            return written

        # Estrai fatti via ASMR reader
        try:
            text = f"USER: {user_msg}\nASSISTANT: {assistant_msg}"
            facts = self.reader.read(text)
        except Exception:
            facts = []

        written["permanent"] = 0
        for f in facts:
            wing = f.get("wing") or "default"
            hall = f.get("hall") or "facts"
            room = f.get("room") or "misc"
            content = f.get("content") or ""
            if not content:
                continue

            # Le directive dell'utente all'agente vanno in PermanentMemory
            # (sono istruzioni cross-sessione inserite in testa al system prompt).
            is_directive = f.get("permanent") is True or hall == "directives"
            if is_directive and self.permanent_memory:
                try:
                    if not _already_in_permanent(self.permanent_memory, content):
                        self.permanent_memory.add(content, mem_type="instruction")
                        written["permanent"] += 1
                except Exception:
                    pass

            # Scrive in medium (ogni fatto è un drawer del room appropriato)
            if self.medium:
                try:
                    self.medium.remember(wing, hall, room, content, kind="drawer")
                    written["medium_rooms"] += 1
                except Exception:
                    pass

            # Scrive in long-term (Qdrant) per fatti "duraturi":
            # preferences + personal + decisions + directives → long-term.
            if self.long and hall in ("personal", "preferences", "decisions", "directives"):
                try:
                    if self.long.add(content, metadata={"wing": wing, "hall": hall, "room": room}):
                        written["long_points"] += 1
                except Exception:
                    pass

        return written


def _user_asked_web(msg: str) -> bool:
    low = msg.lower()
    for pat in WEB_INTENT_PATTERNS:
        if re.search(pat, low):
            return True
    return False


def _already_in_permanent(perm, content: str) -> bool:
    """Evita duplicati quasi-identici nella PermanentMemory."""
    try:
        needle = content.strip().lower()[:80]
        for e in perm.get_all():
            existing = str(e.get("content", "")).strip().lower()[:80]
            if existing == needle:
                return True
            # Se il 70%+ dei caratteri combacia, considera duplicato
            if len(needle) > 20 and needle in existing:
                return True
            if len(existing) > 20 and existing in needle:
                return True
    except Exception:
        pass
    return False


def _looks_like_lookup(msg: str) -> bool:
    """Euristica: la query sembra una richiesta di informazione fattuale?"""
    low = msg.lower().strip()
    if len(low) < 10:
        return False
    lookup_markers = (
        "cos'è", "cos e", "cosa è", "che cos", "chi è", "chi e",
        "quando", "dove", "come si", "quanti", "perché", "perche",
        "spiega", "dimmi", "qual è", "qual e",
    )
    return any(m in low for m in lookup_markers) or low.endswith("?")
