"""ListMemoriesTool — elenca le memorie di tutti i livelli."""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.memory.core_facts import CoreFactsMemory
    from src.memory.medium_term import MediumTermMemory
    from src.memory.qdrant_memory import QdrantMemory


class ListMemoriesTool(BaseTool):
    def __init__(
        self,
        core_facts: "CoreFactsMemory",
        qdrant_memory: "QdrantMemory | None" = None,
        medium_term: "MediumTermMemory | None" = None,
    ):
        self.core = core_facts
        self.qdrant = qdrant_memory
        self.medium = medium_term

    def get_name(self): return "list_memories"

    def get_description(self):
        return "Elenca le memorie salvate: fatti core, lungo termine (Qdrant) e medio termine."

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "tier": {
                    "type": "string",
                    "enum": ["all", "core", "long", "medium"],
                    "description": "Quale livello mostrare (default: all)",
                    "default": "all",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max voci per livello (default: 20)",
                    "default": 20,
                },
            },
        }

    def execute(self, args: dict) -> str:
        tier = args.get("tier", "all")
        limit = max(1, min(100, int(args.get("limit", 20))))
        parts = []

        if tier in ("all", "core"):
            entries = self.core.get_all()
            if entries:
                parts.append("## Fatti core (sempre nel prompt)")
                for e in entries[:limit]:
                    parts.append(f"  [{e['id']}] {e['content']}")
            else:
                parts.append("## Fatti core: nessuno")

        if tier in ("all", "long"):
            if self.qdrant and self.qdrant.is_ready():
                try:
                    count = self.qdrant.count()
                    parts.append(f"\n## Memoria lungo termine — Qdrant ({count} voci totali)")
                    results, _ = self.qdrant.scroll(limit=limit)
                    for r in results:
                        payload = r.get("payload", {})
                        content = payload.get("content", r.get("content", ""))
                        wing = payload.get("wing", "?")
                        hall = payload.get("hall", "?")
                        pid = r.get("id", "?")
                        parts.append(f"  [{pid}] [{wing}/{hall}] {content[:100]}")
                except Exception as e:
                    parts.append(f"\n## Qdrant: errore lettura — {e}")
            else:
                parts.append("\n## Memoria lungo termine: Qdrant non disponibile")

        if tier in ("all", "medium"):
            if self.medium:
                try:
                    stats = self.medium.stats()
                    parts.append(
                        f"\n## Memoria medio termine — SQLite "
                        f"({stats['wings']} wing, {stats['rooms']} room, {stats['drawers']} drawer)"
                    )
                    recent = self.medium.recent_drawers(limit=limit)
                    for d in recent:
                        loc = f"{d.get('wing','?')}/{d.get('hall','?')}/{d.get('room','?')}"
                        parts.append(f"  [{loc}] {d.get('content','')[:100]}")
                except Exception as e:
                    parts.append(f"\n## MediumTerm: errore — {e}")
            else:
                parts.append("\n## Memoria medio termine: non disponibile")

        return "\n".join(parts) if parts else "Nessuna memoria trovata."
