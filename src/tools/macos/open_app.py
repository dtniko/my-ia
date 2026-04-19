from __future__ import annotations
import subprocess
import sys
from ..base_tool import BaseTool


class MacOSOpenAppTool(BaseTool):
    def get_name(self): return "macos_open_app"
    def get_description(self):
        return (
            "Apri un'applicazione macOS o un URL. "
            "USA SEMPRE questo tool quando l'utente chiede di aprire, avviare o lanciare "
            "un'applicazione (es. Spotify, Safari, Terminal, Finder, Xcode, ecc.) o un URL. "
            "NON usare execute_command né read_file per aprire app. "
            "Esempi: target='Spotify', target='Safari', target='https://google.com'"
        )
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Nome dell'applicazione (es. 'Spotify', 'Safari', 'Finder', 'Terminal') "
                        "oppure URL (es. 'https://google.com'). "
                        "Non aggiungere .app o path — solo il nome."
                    ),
                }
            },
            "required": ["target"],
        }

    def execute(self, args: dict) -> str:
        if sys.platform != "darwin":
            return "ERROR: solo macOS"
        target = args.get("target", "")
        if not target:
            return "ERROR: target obbligatorio"
        # URL → `open <url>` ; nome app → `open -a <app>` (senza -a cerca un file)
        is_url = "://" in target
        cmd = ["open", target] if is_url else ["open", "-a", target]
        try:
            subprocess.Popen(cmd)
            return f"Aperto: {target}"
        except Exception as e:
            return f"ERROR: {e}"
