from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from ..base_tool import BaseTool


class RememberTool(BaseTool):
    def get_name(self): return "remember"
    def get_description(self): return "Salva un'istruzione o lezione nella memoria permanente cross-sessione."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Contenuto da ricordare"},
                "type": {"type": "string", "enum": ["instruction", "lesson"], "description": "Tipo di memoria"},
            },
            "required": ["content"],
        }

    def execute(self, args: dict) -> str:
        content = args.get("content", "")
        mem_type = args.get("type", "instruction")
        if not content:
            return "ERROR: content obbligatorio"
        mem_file = Path.home() / ".ltsia" / "memory.json"
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": [], "next_id": 1}
        if mem_file.exists():
            try:
                data = json.loads(mem_file.read_text())
            except Exception:
                pass
        entry = {
            "id": data.get("next_id", 1),
            "date": datetime.now().isoformat(),
            "type": mem_type,
            "content": content,
        }
        data["entries"].append(entry)
        data["next_id"] = entry["id"] + 1
        mem_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return f"Ricordato (id={entry['id']}): {content[:60]}..."
