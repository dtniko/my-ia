from __future__ import annotations
import os
import re
import json
from ..base_tool import BaseTool


class ListDirectoryTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "list_directory"

    def get_description(self) -> str:
        return "Elenca file e directory. Ricorsivo opzionale."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory da listare (default: work_dir)"},
                "recursive": {"type": "boolean", "description": "Lista ricorsiva (default false)"},
            },
        }

    def execute(self, args: dict) -> str:
        path = self._resolve(args.get("path", ""))
        recursive = args.get("recursive", False)
        if not os.path.exists(path):
            return f"ERROR: directory non trovata: {path}"
        if not os.path.isdir(path):
            return f"ERROR: {path} non è una directory"
        try:
            entries = []
            if recursive:
                for root, dirs, files in os.walk(path):
                    # Salta cartelle nascoste e node_modules
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "__pycache__"]
                    for fn in sorted(files):
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, path)
                        size = os.path.getsize(full)
                        entries.append({"path": rel.replace("\\", "/"), "size": size, "is_dir": False})
            else:
                for entry in sorted(os.scandir(path), key=lambda e: e.name):
                    entries.append({
                        "path": entry.name,
                        "size": entry.stat().st_size if not entry.is_dir() else 0,
                        "is_dir": entry.is_dir(),
                    })
            return json.dumps(entries, ensure_ascii=False)
        except Exception as e:
            return f"ERROR: {e}"

    def _resolve(self, path: str) -> str:
        if not path:
            return self.work_dir
        if path.startswith("/"):
            return path
        if re.match(r'^[A-Za-z]:[/\\]', path):
            return path.replace("\\", "/")
        return self.work_dir.rstrip("/") + "/" + path.lstrip("/")
