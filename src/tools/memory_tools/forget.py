from __future__ import annotations
import json
from pathlib import Path
from ..base_tool import BaseTool


class ForgetTool(BaseTool):
    def get_name(self): return "forget"
    def get_description(self): return "Rimuove una voce dalla memoria permanente per ID."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID della memoria da rimuovere"}},
            "required": ["id"],
        }

    def execute(self, args: dict) -> str:
        mem_id = args.get("id")
        if mem_id is None:
            return "ERROR: id obbligatorio"
        mem_file = Path.home() / ".ltsia" / "memory.json"
        if not mem_file.exists():
            return "ERROR: nessuna memoria trovata"
        try:
            data = json.loads(mem_file.read_text())
            before = len(data["entries"])
            data["entries"] = [e for e in data["entries"] if e["id"] != int(mem_id)]
            if len(data["entries"]) == before:
                return f"ERROR: nessuna memoria con id={mem_id}"
            mem_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return f"Memoria id={mem_id} rimossa"
        except Exception as e:
            return f"ERROR: {e}"
