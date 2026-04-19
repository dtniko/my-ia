"""
JobManager — gestisce background job ricorrenti.

Ogni job gira come subprocess Python indipendente (job_worker.py) che:
  - esegue un task a intervallo fisso
  - scrive output in ~/.ltsia/jobs/<id>/outputs/
  - termina quando appare .stop o scade run_until

I job sopravvivono al riavvio di ltsia (il worker gira indipendentemente).
"""
from __future__ import annotations
import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from src.jobs.job import Job
from src.jobs.job_store import JobStore


class JobManager:
    def __init__(self, jobs_dir: str):
        self.store        = JobStore(jobs_dir)
        self._worker_script = self._find_worker_script()

    # ── Public API ────────────────────────────────────────────────────────────

    def create_job(
        self,
        type: str,
        interval_seconds: int,
        description: str,
        params: Optional[dict] = None,
        run_until: Optional[int] = None,
    ) -> str:
        job_id = "job_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        job = Job(
            id=job_id,
            type=type,
            description=description,
            params=params or {},
            interval_seconds=max(10, interval_seconds),
            created_at=int(time.time()),
            run_until=run_until,
            status="active",
        )
        self.store.save(job)
        self._launch_worker(job_id)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        job = self.store.load(job_id)
        if job is None:
            return False
        self.store.request_stop(job_id)
        job.status = "cancelled"
        self.store.save(job)
        return True

    def cancel_all(self) -> None:
        for job in self.store.load_all():
            if job.is_active():
                self.cancel_job(job.id)

    def list_jobs(self) -> list[Job]:
        return self.store.load_all()

    def collect_pending_outputs(self) -> list[dict]:
        return self.store.collect_outputs()

    def restore_active_jobs(self) -> None:
        """Rilancia i worker per i job attivi (da chiamare all'avvio)."""
        if not self._worker_script:
            return
        for job in self.store.load_all():
            if not job.is_active():
                continue
            if job.is_expired():
                job.status = "cancelled"
                self.store.save(job)
                continue
            if self._is_worker_alive(job.id):
                continue
            self._launch_worker(job.id)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _launch_worker(self, job_id: str) -> bool:
        if not self._worker_script:
            return False

        job_dir = self.store.job_dir(job_id)
        (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
        (job_dir / ".stop").unlink(missing_ok=True)   # rimuovi stop stale

        config = {"job_id": job_id, "job_dir": str(job_dir)}

        import json
        stdout_log = open(job_dir / "stdout.log", "w")
        stderr_log = open(job_dir / "stderr.log", "w")

        try:
            subprocess.Popen(
                [sys.executable, self._worker_script, json.dumps(config)],
                stdout=stdout_log,
                stderr=stderr_log,
                start_new_session=True,   # distacca dal processo padre
            )
            return True
        except Exception:
            return False

    def _is_worker_alive(self, job_id: str) -> bool:
        pid = self.store.read_pid(job_id)
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)   # signal 0 = solo verifica esistenza
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _find_worker_script(self) -> str:
        """Trova job_worker.py nella stessa directory di questo file."""
        candidate = Path(__file__).parent / "job_worker.py"
        if candidate.exists():
            return str(candidate)
        return ""
