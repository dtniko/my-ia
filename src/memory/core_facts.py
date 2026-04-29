"""CoreFactsMemory — fatti core in ~/.ltsia/core_facts.md.

Sempre iniettati nel system prompt ad ogni sessione.
Contiene: nome utente, nome IA, preferenze fisse, istruzioni permanenti.
File leggibile e modificabile a mano.

API compatibile con PermanentMemory (get_all / add / remove / format_for_prompt).
"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path


_LTSIA_DIR = Path.home() / ".ltsia"
_FILE = _LTSIA_DIR / "core_facts.md"

_HEADER = "# Fatti core\n\nInformazioni sempre presenti nel contesto dell'IA.\n"


class CoreFactsMemory:
    def __init__(self):
        _LTSIA_DIR.mkdir(parents=True, exist_ok=True)
        if not _FILE.exists():
            _FILE.write_text(_HEADER + "\n", encoding="utf-8")
        self._migrate_from_json()

    # ── Lettura ───────────────────────────────────────────────────────────────

    def get_content(self) -> str:
        return _FILE.read_text(encoding="utf-8")

    def get_all(self) -> list[dict]:
        """Lista di entry compatibile con PermanentMemory per gli orchestratori."""
        entries = []
        for i, line in enumerate(self._parse_lines(), start=1):
            entries.append({"id": i, "type": "instruction", "content": line})
        return entries

    def format_for_prompt(self) -> str:
        lines = self._parse_lines()
        if not lines:
            return ""
        parts = ["## Istruzioni e fatti core\n"]
        parts.extend(f"- {l}" for l in lines)
        return "\n".join(parts)

    # ── Scrittura ─────────────────────────────────────────────────────────────

    def add(self, content: str, mem_type: str = "instruction") -> int:
        """Aggiunge una voce. Ritorna indice (1-based)."""
        content = content.strip()
        if not content:
            return 0
        existing = self._parse_lines()
        # deduplication
        needle = content.lower()
        for ex in existing:
            if ex.lower() == needle or needle in ex.lower() or ex.lower() in needle:
                return 0
        text = _FILE.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        text += f"- {content}\n"
        _FILE.write_text(text, encoding="utf-8")
        return len(existing) + 1

    def remove(self, mem_id: int) -> bool:
        """Rimuove la voce con indice (1-based)."""
        lines = self._parse_lines()
        if mem_id < 1 or mem_id > len(lines):
            return False
        to_remove = lines[mem_id - 1]
        text = _FILE.read_text(encoding="utf-8")
        new_text = re.sub(rf"^- {re.escape(to_remove)}\n?", "", text, flags=re.MULTILINE)
        _FILE.write_text(new_text, encoding="utf-8")
        return True

    def replace_content(self, new_content: str) -> None:
        """Sovrascrive l'intero file (per editing manuale via tool)."""
        _FILE.write_text(new_content, encoding="utf-8")

    # ── Privati ───────────────────────────────────────────────────────────────

    def _parse_lines(self) -> list[str]:
        """Restituisce il testo di ogni voce `- ...` del file."""
        result = []
        for line in _FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                result.append(stripped[2:].strip())
        return result

    def _migrate_from_json(self) -> None:
        """Se memory.json esiste e core_facts.md è vuoto, migra le voci."""
        import json
        json_file = _LTSIA_DIR / "memory.json"
        if not json_file.exists():
            return
        if self._parse_lines():
            return  # già popolato, non sovrascrivere
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            for entry in data.get("entries", []):
                content = entry.get("content", "").strip()
                if content:
                    self.add(content)
            json_file.rename(_LTSIA_DIR / "memory.json.migrated")
        except Exception:
            pass
