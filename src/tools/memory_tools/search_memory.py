from __future__ import annotations
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.memory.semantic_memory import SemanticMemory


class SearchMemoryTool(BaseTool):
    def __init__(self, semantic_memory: "SemanticMemory"):
        self._memory = semantic_memory

    def get_name(self): return "search_memory"

    def get_description(self):
        return (
            "Ricerca semantica nella memoria locale (SQLite + embedding). "
            "Trova memorie rilevanti per significato, non solo per parole chiave. "
            "Usa prima di rispondere a domande complesse per recuperare contesto rilevante."
        )

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Cosa cercare nella memoria. Query in linguaggio naturale.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero massimo di risultati (default: 5, max: 20)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def execute(self, args: dict) -> str:
        query = args.get("query", "").strip()
        if not query:
            return "ERROR: query è obbligatoria."

        limit   = max(1, min(20, int(args.get("limit", 5))))
        results = self._memory.search(query, limit)

        if not results:
            return f'Nessuna memoria trovata per: "{query}"'

        lines = [f'Memorie rilevanti per "{query}" (trovate {len(results)}):\n']
        for i, r in enumerate(results, 1):
            score   = f" [{r['score']*100:.0f}%]" if "score" in r else ""
            content = r.get("content", "(nessun contenuto)")
            lines.append(f"{i}.{score} {content}")

        return "\n".join(lines)
