"""RememberTool — salva informazioni nel livello di memoria appropriato.

Routing:
  core   → ~/.ltsia/core_facts.md   (sempre iniettato nel prompt)
  long   → Qdrant                   (lungo termine, ricerca semantica)
  medium → MediumTermMemory SQLite  (48h, poi valutato per promozione)
  auto   → l'IA decide in base al contenuto (default)
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import TYPE_CHECKING

from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.memory.core_facts import CoreFactsMemory
    from src.memory.medium_term import MediumTermMemory
    from src.memory.qdrant_memory import QdrantMemory


# Parole chiave che suggeriscono un fatto "core" (identità/preferenze fisse)
_CORE_HINTS = re.compile(
    r"\b(mi chiamo|il mio nome|chiamami|sono|preferisco|il mio stile|"
    r"voglio che tu|devi sempre|non fare mai|usa sempre|lavoro come|"
    r"l.ia si chiama|il tuo nome)\b",
    re.IGNORECASE | re.UNICODE,
)

# Parole chiave che suggeriscono contesto temporaneo (medium-term)
_MEDIUM_HINTS = re.compile(
    r"\b(oggi|ieri|questa settimana|questo mese|sto lavorando|"
    r"abbiamo fatto|abbiamo deciso|temporaneamente|al momento|"
    r"per ora|questo progetto|questa sessione)\b",
    re.IGNORECASE | re.UNICODE,
)


def _auto_route(content: str) -> str:
    """Euristica veloce per determinare il livello senza chiamare l'LLM."""
    if _CORE_HINTS.search(content):
        return "core"
    if _MEDIUM_HINTS.search(content):
        return "medium"
    return "long"


class RememberTool(BaseTool):
    def __init__(
        self,
        core_facts: "CoreFactsMemory",
        qdrant_memory: "QdrantMemory | None" = None,
        medium_term: "MediumTermMemory | None" = None,
    ):
        self.core = core_facts
        self.qdrant = qdrant_memory
        self.medium = medium_term

    def get_name(self): return "remember"

    def get_description(self):
        return (
            "Salva un'informazione nella memoria appropriata. "
            "Usa tier='core' per fatti sull'identità/preferenze sempre validi; "
            "tier='long' per fatti persistenti da ricordare a lungo; "
            "tier='medium' per informazioni di contesto (ultime 48h); "
            "tier='auto' (default) per lasciar decidere automaticamente."
        )

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Informazione da ricordare",
                },
                "tier": {
                    "type": "string",
                    "enum": ["auto", "core", "long", "medium"],
                    "description": "Livello di memoria (default: auto)",
                    "default": "auto",
                },
                "wing": {
                    "type": "string",
                    "description": "Wing per medium-term (es. 'utente:roque', 'progetto:ltsia')",
                },
                "hall": {
                    "type": "string",
                    "description": "Hall per medium-term (es. 'preferences', 'events', 'facts')",
                },
                "room": {
                    "type": "string",
                    "description": "Room per medium-term (argomento specifico)",
                },
            },
            "required": ["content"],
        }

    def execute(self, args: dict) -> str:
        content = args.get("content", "").strip()
        if not content:
            return "ERROR: content obbligatorio"

        tier = args.get("tier", "auto")
        if tier == "auto":
            tier = _auto_route(content)

        if tier == "core":
            idx = self.core.add(content)
            if idx == 0:
                return f"Già presente nei fatti core: {content[:60]}"
            return f"Salvato nei fatti core (#{idx}): {content[:60]}"

        if tier == "long":
            if self.qdrant and self.qdrant.is_ready():
                mem_id = self.qdrant.add(
                    content,
                    metadata={
                        "wing": args.get("wing", "general"),
                        "hall": args.get("hall", "facts"),
                        "room": args.get("room", ""),
                        "source": "remember_tool",
                        "created_at": datetime.now().isoformat(),
                    },
                )
                if mem_id:
                    return f"Salvato in memoria lungo termine (Qdrant): {content[:60]}"
                return f"Qdrant non disponibile — salvato nei fatti core: {content[:60]}"
            # fallback a core se Qdrant non disponibile
            self.core.add(content)
            return f"Qdrant non disponibile — salvato nei fatti core: {content[:60]}"

        if tier == "medium":
            if self.medium:
                wing = args.get("wing", "sessione")
                hall = args.get("hall", "fatti")
                room = args.get("room", "generale")
                self.medium.remember(wing, hall, room, content, kind="drawer")
                return f"Salvato in memoria medio termine (48h) [{wing}/{hall}/{room}]: {content[:60]}"
            # fallback a long
            if self.qdrant and self.qdrant.is_ready():
                self.qdrant.add(content, metadata={"source": "remember_tool_medium_fallback"})
                return f"MediumTerm non disponibile — salvato in Qdrant: {content[:60]}"
            self.core.add(content)
            return f"Fallback a core: {content[:60]}"

        return f"ERROR: tier sconosciuto '{tier}'"
