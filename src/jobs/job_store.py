"""
JobStore — persistenza dei job su disco come file JSON.

Layout directory:
  ~/.ltsia/jobs/<id>/job.json          — definizione job
  ~/.ltsia/jobs/<id>/.stop             — flag cancellazione
  ~/.ltsia/jobs/<id>/.running          — PID del worker
  ~/.ltsia/jobs/<id>/outputs/<ts>.json — notifiche output
  ~/.ltsia/jobs/<id>/worker.log        — log del worker
"""
from __future__ import annotations
import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from src.jobs.job import Job


class JobStore:
    def __init__(self, jobs_dir: str):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def save(self, job: Job) -> None:
        d = self.jobs_dir / job.id
        (d / "outputs").mkdir(parents=True, exist_ok=True)
        tmp  = d / "job.json.tmp"
        dest = d / "job.json"
        tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
        tmp.replace(dest)

    def load(self, job_id: str) -> Optional[Job]:
        f = self.jobs_dir / job_id / "job.json"
        if not f.exists():
            return None
        try:
            return Job.from_dict(json.loads(f.read_text()))
        except Exception:
            return None

    def load_all(self) -> list[Job]:
        jobs = []
        for entry in sorted(self.jobs_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            job = self.load(entry.name)
            if job:
                jobs.append(job)
        return jobs

    def delete(self, job_id: str) -> None:
        d = self.jobs_dir / job_id
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)

    # ── Stop flag ─────────────────────────────────────────────────────────────

    def request_stop(self, job_id: str) -> None:
        stop = self.jobs_dir / job_id / ".stop"
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("1")

    def is_stop_requested(self, job_id: str) -> bool:
        return (self.jobs_dir / job_id / ".stop").exists()

    # ── PID / alive check ─────────────────────────────────────────────────────

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def read_pid(self, job_id: str) -> int:
        pid_file = self.jobs_dir / job_id / ".running"
        if not pid_file.exists():
            return 0
        try:
            return int(pid_file.read_text().strip())
        except Exception:
            return 0

    # ── Output notifications ──────────────────────────────────────────────────

    def collect_outputs(self) -> list[dict]:
        """Legge e consuma tutti gli output pendenti di tutti i job."""
        results = []
        for job_dir in sorted(self.jobs_dir.iterdir()):
            if not job_dir.is_dir() or job_dir.name.startswith("."):
                continue
            out_dir = job_dir / "outputs"
            if not out_dir.exists():
                continue
            for f in sorted(out_dir.iterdir()):
                if f.suffix != ".json":
                    continue
                try:
                    data = json.loads(f.read_text())
                    if isinstance(data, dict):
                        results.append(data)
                except Exception:
                    pass
                f.unlink(missing_ok=True)

        results.sort(key=lambda x: x.get("produced_at", 0))
        return results
