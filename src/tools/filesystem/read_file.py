from __future__ import annotations
import os
from ..base_tool import BaseTool


class ReadFileTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "read_file"

    def get_description(self) -> str:
        return (
            "Leggi il contenuto testuale di un FILE su disco. "
            "Path relativo a work_dir o assoluto. "
            "NON usare per aprire applicazioni (Spotify, Safari, ecc.) — usa macos_open_app."
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Percorso del file"},
                "max_bytes": {"type": "integer", "description": "Dimensione massima in bytes (default 32768)"},
            },
            "required": ["path"],
        }

    def execute(self, args: dict) -> str:
        path = self._resolve(args.get("path", ""))
        max_bytes = int(args.get("max_bytes", 32768))
        if not path:
            return "ERROR: path obbligatorio"
        if not os.path.exists(path):
            return f"ERROR: file non trovato: {path}"
        if os.path.isdir(path):
            return f"ERROR: {path} è una directory"
        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_bytes)
            result = content
            if size > max_bytes:
                result += f"\n[... file troncato — {size} bytes totali, letti {max_bytes}]"
            return result
        except Exception as e:
            return f"ERROR: {e}"

    def _resolve(self, path: str) -> str:
        if not path:
            return self.work_dir
        if path.startswith("/"):
            return path
        import re
        if re.match(r'^[A-Za-z]:[/\\]', path):
            return path.replace("\\", "/")
        return self.work_dir.rstrip("/") + "/" + path.lstrip("/")
