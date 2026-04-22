"""
QdrantLifecycle — avvia Qdrant come subprocess figlio se non è già in ascolto
su host:port, e lo spegne all'uscita del processo LTSIA.

Non tocca un Qdrant avviato esternamente: il kill avviene solo se il processo
è stato spawnato da noi (flag `owned`).

Binario atteso in: ~/.ltsia/qdrant/qdrant
"""
from __future__ import annotations
import atexit
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional


class QdrantLifecycle:
    def __init__(self, host: str, port: int, qdrant_dir: Optional[Path] = None):
        self.host = host
        self.port = port
        self.qdrant_dir = qdrant_dir or (Path.home() / ".ltsia" / "qdrant")
        self.process: Optional[subprocess.Popen] = None
        self.owned = False
        self._atexit_registered = False

    def is_running(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except OSError:
            return False

    def start_if_needed(self, timeout: float = 15.0) -> tuple[bool, str]:
        """Avvia Qdrant se non già attivo.

        Ritorna (ok, status) dove status ∈ {"already", "started", "missing", "timeout", "failed"}.
        """
        if self.is_running():
            return True, "already"

        qdrant_bin = self.qdrant_dir / "qdrant"
        if not qdrant_bin.exists():
            return False, "missing"

        env = os.environ.copy()
        env["QDRANT__STORAGE__STORAGE_PATH"] = str(self.qdrant_dir / "storage")
        env["QDRANT__STORAGE__SNAPSHOTS_PATH"] = str(self.qdrant_dir / "snapshots")
        env["QDRANT__SERVICE__STATIC_CONTENT_DIR"] = str(self.qdrant_dir / "static")

        log_path = self.qdrant_dir / "qdrant.log"
        log_file = open(log_path, "ab")

        try:
            self.process = subprocess.Popen(
                [str(qdrant_bin)],
                cwd=str(self.qdrant_dir),
                stdout=log_file,
                stderr=log_file,
                env=env,
                start_new_session=True,
            )
        except OSError:
            log_file.close()
            return False, "failed"

        self.owned = True
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                return False, "failed"
            if self.is_running():
                return True, "started"
            time.sleep(0.3)
        return False, "timeout"

    def stop(self) -> None:
        if not self.owned or self.process is None:
            return
        if self.process.poll() is not None:
            self.owned = False
            return
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        finally:
            self.owned = False
