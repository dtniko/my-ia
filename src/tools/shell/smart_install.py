"""Tool per installare dipendenze rilevando il package manager."""
from __future__ import annotations
import os
import subprocess
from ..base_tool import BaseTool


class SmartInstallTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self) -> str:
        return "install_packages"

    def get_description(self) -> str:
        return "Installa dipendenze rilevando automaticamente il package manager (npm, pip, composer, etc.)."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "packages": {"type": "array", "items": {"type": "string"}, "description": "Pacchetti da installare"},
                "cwd": {"type": "string", "description": "Directory (default: work_dir)"},
                "manager": {"type": "string", "enum": ["npm", "pip", "pip3", "composer", "brew", "apt", "auto"]},
            },
            "required": ["packages"],
        }

    def execute(self, args: dict) -> str:
        packages = args.get("packages", [])
        cwd = args.get("cwd") or self.work_dir
        manager = args.get("manager", "auto")

        if not packages:
            return "ERROR: packages obbligatorio"

        if manager == "auto":
            manager = self._detect_manager(cwd)

        if not manager:
            return "ERROR: nessun package manager rilevato"

        pkg_str = " ".join(packages)
        if manager == "npm":
            cmd = f"npm install {pkg_str}"
        elif manager in ("pip", "pip3"):
            cmd = f"{manager} install {pkg_str}"
        elif manager == "composer":
            cmd = f"composer require {pkg_str}"
        elif manager == "brew":
            cmd = f"brew install {pkg_str}"
        elif manager == "apt":
            cmd = f"apt-get install -y {pkg_str}"
        else:
            return f"ERROR: manager sconosciuto: {manager}"

        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=300
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                return f"ERROR (exit {result.returncode}):\n{output}"
            return f"Installati con {manager}:\n{output}"
        except Exception as e:
            return f"ERROR: {e}"

    def _detect_manager(self, cwd: str) -> str:
        if os.path.exists(os.path.join(cwd, "package.json")):
            return "npm"
        if os.path.exists(os.path.join(cwd, "requirements.txt")) or os.path.exists(os.path.join(cwd, "setup.py")):
            return "pip3"
        if os.path.exists(os.path.join(cwd, "composer.json")):
            return "composer"
        return "pip3"
