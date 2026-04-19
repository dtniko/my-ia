from __future__ import annotations
import json
from pathlib import Path
from ..base_tool import BaseTool


class ListMemoriesTool(BaseTool):
    def get_name(self): return "list_memories"
    def get_description(self): return "Elenca tutte le memorie permanenti salvate."
    def get_parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> str:
        mem_file = Path.home() / ".ltsia" / "memory.json"
        if not mem_file.exists():
            return "Nessuna memoria salvata."
        try:
            data = json.loads(mem_file.read_text())
            entries = data.get("entries", [])
            if not entries:
                return "Nessuna memoria salvata."
            lines = []
            for e in entries:
                lines.append(f"[{e['id']}] ({e['type']}) {e['content'][:80]}")
            return "\n".join(lines)
        except Exception as e:
            return f"ERROR: {e}"
