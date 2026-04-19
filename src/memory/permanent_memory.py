"""Memoria permanente cross-sessione in ~/.ltsia/memory.json."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class PermanentMemory:
    def __init__(self):
        self.mem_file = Path.home() / ".ltsia" / "memory.json"
        self.mem_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.mem_file.exists():
            try:
                return json.loads(self.mem_file.read_text())
            except Exception:
                pass
        return {"entries": [], "next_id": 1}

    def _save(self):
        self.mem_file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def add(self, content: str, mem_type: str = "instruction") -> int:
        entry = {
            "id": self._data["next_id"],
            "date": datetime.now().isoformat(),
            "type": mem_type,
            "content": content,
        }
        self._data["entries"].append(entry)
        self._data["next_id"] = entry["id"] + 1
        self._save()
        return entry["id"]

    def remove(self, mem_id: int) -> bool:
        before = len(self._data["entries"])
        self._data["entries"] = [e for e in self._data["entries"] if e["id"] != mem_id]
        if len(self._data["entries"]) < before:
            self._save()
            return True
        return False

    def get_all(self) -> list[dict]:
        return self._data.get("entries", [])

    def format_for_prompt(self) -> str:
        entries = self.get_all()
        if not entries:
            return ""
        lines = ["## Memorie persistenti\n"]
        for e in entries:
            lines.append(f"- [{e['type']}] {e['content']}")
        return "\n".join(lines)
