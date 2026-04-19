from __future__ import annotations
import subprocess
import sys
from ..base_tool import BaseTool


class MacOSClipboardTool(BaseTool):
    def get_name(self): return "macos_clipboard"
    def get_description(self): return "Leggi o scrivi negli appunti macOS."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write"]},
                "text": {"type": "string", "description": "Testo da scrivere (solo per write)"},
            },
            "required": ["action"],
        }

    def execute(self, args: dict) -> str:
        if sys.platform != "darwin":
            return "ERROR: solo macOS"
        action = args.get("action", "read")
        if action == "read":
            try:
                r = subprocess.run(["pbpaste"], capture_output=True, text=True)
                return r.stdout
            except Exception as e:
                return f"ERROR: {e}"
        elif action == "write":
            text = args.get("text", "")
            try:
                subprocess.run(["pbcopy"], input=text, text=True, check=True)
                return "Testo copiato negli appunti"
            except Exception as e:
                return f"ERROR: {e}"
        return "ERROR: action deve essere read o write"
