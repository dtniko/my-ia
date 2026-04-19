from __future__ import annotations
import os
import re
from ..base_tool import BaseTool


class WriteFileTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "write_file"

    def get_description(self) -> str:
        return "Scrivi contenuto su file. Crea le directory parent se necessario. Opzionalmente appendi."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Percorso del file"},
                "content": {"type": "string", "description": "Contenuto da scrivere"},
                "append": {"type": "boolean", "description": "Se true, aggiunge in fondo invece di sovrascrivere"},
            },
            "required": ["path", "content"],
        }

    def execute(self, args: dict) -> str:
        path = self._resolve(args.get("path", ""))
        content = args.get("content", "")
        append = args.get("append", False)
        if not path:
            return "ERROR: path obbligatorio"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            action = "aggiunto a" if append else "scritto"
            return f"File {action}: {path} ({len(content)} bytes)"
        except Exception as e:
            return f"ERROR: {e}"

    def _resolve(self, path: str) -> str:
        if not path:
            return ""
        if path.startswith("/"):
            return path
        if re.match(r'^[A-Za-z]:[/\\]', path):
            return path.replace("\\", "/")
        return self.work_dir.rstrip("/") + "/" + path.lstrip("/")
