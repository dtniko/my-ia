from __future__ import annotations
import os
import subprocess
import sys
from ..base_tool import BaseTool


class MacOSScreenshotTool(BaseTool):
    def __init__(self, work_dir: str = "/tmp"):
        self.work_dir = work_dir

    def get_name(self): return "macos_screenshot"
    def get_description(self): return "Cattura uno screenshot su macOS."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {"output_path": {"type": "string", "description": "Path output (default: work_dir/screenshot.png)"}},
        }

    def execute(self, args: dict) -> str:
        if sys.platform != "darwin":
            return "ERROR: solo macOS"
        out = args.get("output_path") or os.path.join(self.work_dir, "screenshot.png")
        try:
            r = subprocess.run(["screencapture", "-x", out], capture_output=True, text=True)
            if r.returncode != 0:
                return f"ERROR: {r.stderr}"
            return f"Screenshot salvato: {out}"
        except Exception as e:
            return f"ERROR: {e}"
