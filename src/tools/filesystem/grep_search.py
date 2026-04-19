from __future__ import annotations
import os
import re
import json
from ..base_tool import BaseTool


class GrepSearchTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "grep_search"

    def get_description(self) -> str:
        return "Cerca regex in file. Ritorna lista di {file, line, content}."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern regex"},
                "path": {"type": "string", "description": "File o directory (default: work_dir)"},
                "glob": {"type": "string", "description": "Filtro file (es. *.py)"},
                "case_insensitive": {"type": "boolean"},
            },
            "required": ["pattern"],
        }

    def execute(self, args: dict) -> str:
        import glob as globmod
        pattern = args.get("pattern", "")
        path = self._resolve(args.get("path", ""))
        file_glob = args.get("glob", "*")
        case_insensitive = args.get("case_insensitive", False)
        if not pattern:
            return "ERROR: pattern obbligatorio"
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"ERROR: regex non valida: {e}"

        files_to_search = []
        if os.path.isfile(path):
            files_to_search = [path]
        else:
            search_pattern = os.path.join(path, "**", file_glob)
            files_to_search = globmod.glob(search_pattern, recursive=True)

        results = []
        for filepath in sorted(files_to_search):
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(filepath, self.work_dir).replace("\\", "/")
                            results.append({"file": rel, "line": lineno, "content": line.rstrip()})
                            if len(results) >= 200:
                                break
            except Exception:
                continue
            if len(results) >= 200:
                break

        return json.dumps(results, ensure_ascii=False)

    def _resolve(self, path: str) -> str:
        if not path:
            return self.work_dir
        if path.startswith("/"):
            return path
        if re.match(r'^[A-Za-z]:[/\\]', path):
            return path.replace("\\", "/")
        return self.work_dir.rstrip("/") + "/" + path.lstrip("/")
