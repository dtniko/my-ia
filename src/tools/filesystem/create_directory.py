from __future__ import annotations
import os
import re
from ..base_tool import BaseTool


class CreateDirectoryTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "create_directory"

    def get_description(self) -> str:
        return "Crea una directory (e tutti i parent necessari)."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Percorso directory da creare"},
            },
            "required": ["path"],
        }

    def execute(self, args: dict) -> str:
        path = self._resolve(args.get("path", ""))
        if not path:
            return "ERROR: path obbligatorio"
        try:
            os.makedirs(path, exist_ok=True)
            return f"Directory creata: {path}"
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
