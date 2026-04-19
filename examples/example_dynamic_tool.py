"""
ESEMPIO: Tool dinamico creabile/caricabile a runtime.

Questo file mostra come l'IA può creare un nuovo tool Python
che viene caricato IMMEDIATAMENTE senza riavviare il processo.

Per creare questo tool a runtime, l'IA usa il tool `create_module`:
  {
    "module_name": "git_tool",
    "code": "... contenuto di questo file ...",
    "description": "Tool per operazioni Git"
  }

Dopo la chiamata, il tool 'git_status' sarà disponibile immediatamente.
"""
import subprocess
import os

# Questo import funziona sia quando il file è in src/tools/ che quando è caricato dinamicamente
try:
    from src.tools.base_tool import BaseTool
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from src.tools.base_tool import BaseTool


class GitStatusTool(BaseTool):
    """Tool esempio: ottiene git status di una directory."""

    def get_name(self) -> str:
        return "git_status"

    def get_description(self) -> str:
        return "Ottieni lo stato git di una directory (git status, log, diff)."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path del repository git"},
                "command": {
                    "type": "string",
                    "enum": ["status", "log", "diff", "branch"],
                    "description": "Sottocomando git (default: status)",
                },
            },
        }

    def execute(self, args: dict) -> str:
        path = args.get("path", ".")
        command = args.get("command", "status")
        git_cmd = {"status": "git status", "log": "git log --oneline -10", "diff": "git diff", "branch": "git branch -a"}
        cmd = git_cmd.get(command, "git status")
        try:
            r = subprocess.run(cmd, shell=True, cwd=path, capture_output=True, text=True, timeout=10)
            return r.stdout + (r.stderr if r.returncode != 0 else "")
        except Exception as e:
            return f"ERROR: {e}"
