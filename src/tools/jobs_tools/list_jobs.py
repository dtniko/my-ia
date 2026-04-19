from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.jobs.job_manager import JobManager


class ListJobsTool(BaseTool):
    def __init__(self, manager: "JobManager"):
        self._manager = manager

    def get_name(self): return "list_jobs"

    def get_description(self):
        return "Elenca tutti i background job (attivi e cancellati) con stato, intervallo e descrizione."

    def get_parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    def execute(self, args: dict) -> str:
        jobs = self._manager.list_jobs()
        if not jobs:
            return "Nessun job registrato. Usa schedule_job per crearne uno."

        lines = []
        for job in jobs:
            status_icon = {
                "active":    "[ATTIVO]",
                "cancelled": "[CANCELLATO]",
                "crashed":   "[CRASH]",
            }.get(job.status, "[?]")

            interval_min = round(job.interval_seconds / 60, 1)
            created_str  = datetime.fromtimestamp(job.created_at).strftime("%d/%m %H:%M")
            until_str    = (
                f" — stop alle {datetime.fromtimestamp(job.run_until).strftime('%H:%M')}"
                if job.run_until else ""
            )

            lines += [
                f"{status_icon} {job.id}",
                f"  Tipo:        {job.type}",
                f"  Descrizione: {job.description}",
                f"  Intervallo:  ogni {interval_min} min{until_str}",
                f"  Creato:      {created_str}  —  eseguito {job.run_count} volte",
                "",
            ]

        return "\n".join(lines)
