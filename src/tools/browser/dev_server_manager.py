"""Gestisce processi server in background."""
from __future__ import annotations
import os
import subprocess
import uuid
from typing import Optional


class DevServerManager:
    _servers: dict[str, dict] = {}

    @classmethod
    def start(cls, command: str, cwd: str, port: int) -> Optional[str]:
        server_id = str(uuid.uuid4())[:8]
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cls._servers[server_id] = {
                "id": server_id,
                "command": command,
                "cwd": cwd,
                "port": port,
                "url": f"http://localhost:{port}",
                "proc": proc,
            }
            return server_id
        except Exception:
            return None

    @classmethod
    def stop(cls, server_id: str) -> bool:
        srv = cls._servers.pop(server_id, None)
        if not srv:
            return False
        try:
            srv["proc"].terminate()
            srv["proc"].wait(timeout=5)
        except Exception:
            try:
                srv["proc"].kill()
            except Exception:
                pass
        return True

    @classmethod
    def stop_all(cls):
        for sid in list(cls._servers.keys()):
            cls.stop(sid)

    @classmethod
    def find_by_command(cls, command: str) -> Optional[dict]:
        for srv in cls._servers.values():
            if srv["command"] == command:
                return srv
        return None

    @classmethod
    def list_servers(cls) -> list[dict]:
        return [
            {"id": s["id"], "command": s["command"], "url": s["url"]}
            for s in cls._servers.values()
        ]
