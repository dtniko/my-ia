from __future__ import annotations
import subprocess
import sys
from ..base_tool import BaseTool


class MacOSAppleScriptTool(BaseTool):
    def get_name(self): return "macos_applescript"
    def get_description(self): return "Esegui uno script AppleScript su macOS."
    def get_parameters(self):
        return {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}

    def execute(self, args: dict) -> str:
        if sys.platform != "darwin":
            return "ERROR: solo macOS"
        script = args.get("script", "")
        if not script:
            return "ERROR: script obbligatorio"
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return f"ERROR: {r.stderr}"
            return r.stdout.strip() or "OK"
        except Exception as e:
            return f"ERROR: {e}"
