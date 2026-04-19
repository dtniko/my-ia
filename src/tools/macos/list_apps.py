from __future__ import annotations
import subprocess
import sys
from ..base_tool import BaseTool


class MacOSListAppsTool(BaseTool):
    def get_name(self): return "macos_list_apps"
    def get_description(self): return "Elenca le applicazioni in esecuzione su macOS."
    def get_parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> str:
        if sys.platform != "darwin":
            return "ERROR: solo macOS"
        try:
            r = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'],
                capture_output=True, text=True, timeout=10
            )
            return r.stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"
