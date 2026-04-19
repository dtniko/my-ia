from __future__ import annotations
import os
import re
import shutil
from ..base_tool import BaseTool


class MoveFileTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "move_file"

    def get_description(self) -> str:
        return "Sposta o rinomina un file/directory."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["source", "destination"],
        }

    def execute(self, args: dict) -> str:
        src = self._resolve(args.get("source", ""))
        dst = self._resolve(args.get("destination", ""))
        if not src or not dst:
            return "ERROR: source e destination obbligatori"
        if not os.path.exists(src):
            return f"ERROR: source non trovato: {src}"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            shutil.move(src, dst)
            return f"Spostato: {src} → {dst}"
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
