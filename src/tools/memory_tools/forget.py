"""ForgetTool — rimuove voci dalla memoria (core o Qdrant)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.memory.core_facts import CoreFactsMemory
    from src.memory.qdrant_memory import QdrantMemory


class ForgetTool(BaseTool):
    def __init__(
        self,
        core_facts: "CoreFactsMemory",
        qdrant_memory: "QdrantMemory | None" = None,
    ):
        self.core = core_facts
        self.qdrant = qdrant_memory

    def get_name(self): return "forget"

    def get_description(self):
        return (
            "Rimuove una voce dalla memoria. "
            "Usa tier='core' + id numerico per i fatti core; "
            "tier='long' + id (UUID stringa) per Qdrant."
        )

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "id": {
                    "description": "ID della memoria (int per core, UUID string per long)",
                },
                "tier": {
                    "type": "string",
                    "enum": ["core", "long"],
                    "description": "Livello da cui rimuovere (default: core)",
                    "default": "core",
                },
            },
            "required": ["id"],
        }

    def execute(self, args: dict) -> str:
        tier = args.get("tier", "core")
        mem_id = args.get("id")
        if mem_id is None:
            return "ERROR: id obbligatorio"

        if tier == "core":
            try:
                idx = int(mem_id)
            except (ValueError, TypeError):
                return "ERROR: id deve essere un intero per tier=core"
            if self.core.remove(idx):
                return f"Rimosso dai fatti core (#{idx})"
            return f"ERROR: nessun fatto core con id={idx}"

        if tier == "long":
            if not (self.qdrant and self.qdrant.is_ready()):
                return "ERROR: Qdrant non disponibile"
            try:
                self.qdrant.delete(str(mem_id))
                return f"Rimosso da Qdrant (id={mem_id})"
            except Exception as e:
                return f"ERROR Qdrant: {e}"

        return f"ERROR: tier sconosciuto '{tier}'"
