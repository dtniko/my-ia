from __future__ import annotations
import fnmatch
import os
import re
import json
from ..base_tool import BaseTool


class GlobSearchTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "glob_search"

    def get_description(self) -> str:
        return "Cerca file con pattern glob (es. src/**/*.py, *.json). Ritorna lista di path."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern glob"},
                "base_dir": {"type": "string", "description": "Directory base (default: work_dir)"},
            },
            "required": ["pattern"],
        }

    def execute(self, args: dict) -> str:
        import glob
        pattern = args.get("pattern", "")
        base = self._resolve(args.get("base_dir", ""))
        if not pattern:
            return "ERROR: pattern obbligatorio"
        # Se il pattern non è assoluto, prefissa con base
        if not os.path.isabs(pattern):
            full_pattern = os.path.join(base, pattern)
        else:
            full_pattern = pattern
        matches = glob.glob(full_pattern, recursive=True)
        # Ritorna path relativi a base
        result = []
        for m in sorted(matches):
            try:
                rel = os.path.relpath(m, base).replace("\\", "/")
            except ValueError:
                rel = m
            result.append(rel)
        return json.dumps(result, ensure_ascii=False)

    def _resolve(self, path: str) -> str:
        if not path:
            return self.work_dir
        if path.startswith("/"):
            return path
        if re.match(r'^[A-Za-z]:[/\\]', path):
            return path.replace("\\", "/")
        return self.work_dir.rstrip("/") + "/" + path.lstrip("/")
