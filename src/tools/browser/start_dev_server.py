"""Avvia un dev server in background e restituisce l'URL."""
from __future__ import annotations
import os
import re
import subprocess
import time
from ..base_tool import BaseTool
from .dev_server_manager import DevServerManager


class StartDevServerTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self): return "start_dev_server"
    def get_description(self): return "Avvia un dev server in background. Ritorna server_id e URL."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando server (es. 'npm run dev', 'python -m http.server 3000')"},
                "cwd": {"type": "string", "description": "Directory (default: work_dir)"},
                "port": {"type": "integer", "description": "Porta (default: 3000)"},
            },
            "required": ["command"],
        }

    def execute(self, args: dict) -> str:
        command = args.get("command", "")
        cwd = args.get("cwd") or self.work_dir
        port = int(args.get("port", 3000))
        if not command:
            return "ERROR: command obbligatorio"

        # Controlla se già in esecuzione
        existing = DevServerManager.find_by_command(command)
        if existing:
            return f"Server già in esecuzione — id={existing['id']}, url={existing['url']}"

        server_id = DevServerManager.start(command, cwd, port)
        if not server_id:
            return "ERROR: impossibile avviare il server"

        # Attendi avvio
        url = f"http://localhost:{port}"
        time.sleep(2)
        return f"Server avviato — id={server_id}, url={url}"
