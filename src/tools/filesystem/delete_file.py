from __future__ import annotations
import os
import re
import shutil
from ..base_tool import BaseTool


class DeleteFileTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "delete_file"

    def get_description(self) -> str:
        return "Cancella un file o una directory (ricorsivo)."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Percorso da cancellare"},
            },
            "required": ["path"],
        }

    def execute(self, args: dict) -> str:
        path = self._resolve(args.get("path", ""))
        if not path:
            return "ERROR: path obbligatorio"
        if not os.path.exists(path):
            return f"ERROR: non trovato: {path}"
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                return f"Directory rimossa: {path}"
            else:
                os.remove(path)
                return f"File rimosso: {path}"
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
