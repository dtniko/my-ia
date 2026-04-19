"""Tool per eseguire comandi shell con timeout e sicurezza."""
from __future__ import annotations
import os
import re
import subprocess
import sys
from typing import Callable, Optional
from ..base_tool import BaseTool

BLOCKED_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'dd\s+if=',
    r':\(\)\s*\{',  # fork bomb
    r'mkfs\.',
    r'fdisk',
    r'format\s+[A-Za-z]:',
]

SERVER_PATTERNS = [
    r'npm\s+run\s+dev',
    r'npm\s+start',
    r'vite\b',
    r'flask\s+run',
    r'python.*manage\.py\s+runserver',
    r'php\s+-S\b',
    r'live-server',
    r'http-server',
]


class ExecuteCommandTool(BaseTool):
    def __init__(self, work_dir: str, output_callback: Optional[Callable[[str], None]] = None):
        self.work_dir = work_dir
        self.output_callback = output_callback

    def get_name(self) -> str:
        return "execute_command"

    def get_description(self) -> str:
        return "Esegui un comando shell. Timeout default 60s. Non usare per server long-running — usa start_dev_server."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando da eseguire"},
                "cwd": {"type": "string", "description": "Directory di lavoro (default: work_dir)"},
                "timeout": {"type": "integer", "description": "Timeout in secondi (default 60)"},
                "env": {"type": "object", "description": "Variabili d'ambiente extra"},
            },
            "required": ["command"],
        }

    def execute(self, args: dict) -> str:
        cmd = args.get("command", "")
        cwd = args.get("cwd") or self.work_dir
        timeout = int(args.get("timeout", 60))
        env_extra = args.get("env") or {}

        if not cmd:
            return "ERROR: command obbligatorio"

        # Sicurezza: blocca comandi pericolosi
        for bp in BLOCKED_PATTERNS:
            if re.search(bp, cmd, re.IGNORECASE):
                return f"ERROR: comando bloccato per sicurezza: {cmd}"

        # Avvisa per server long-running
        for sp in SERVER_PATTERNS:
            if re.search(sp, cmd, re.IGNORECASE):
                return f"ERROR: usa start_dev_server per server long-running (rilevato: {cmd})"

        # Build environment
        env = os.environ.copy()
        env.update(env_extra)

        # Shell platform
        shell = True
        if sys.platform == "win32":
            shell = True

        try:
            proc = subprocess.Popen(
                cmd,
                shell=shell,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            output_lines = []
            try:
                for line in iter(proc.stdout.readline, ""):
                    output_lines.append(line)
                    if self.output_callback:
                        self.output_callback(line)
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                output_lines.append(f"\n[TIMEOUT dopo {timeout}s]")

            output = "".join(output_lines)
            exit_code = proc.returncode or 0
            result = f"Exit code: {exit_code}\n{output}"
            return self.truncate(result)
        except Exception as e:
            return f"ERROR: {e}"
