from __future__ import annotations
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.jobs.job_manager import JobManager


class CancelJobTool(BaseTool):
    def __init__(self, manager: "JobManager"):
        self._manager = manager

    def get_name(self): return "cancel_job"

    def get_description(self):
        return (
            "Cancella un background job in esecuzione. "
            "Usa job_id da schedule_job o list_jobs. "
            "Passa job_id='all' per cancellare tutti i job attivi."
        )

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "ID del job da cancellare, o 'all' per cancellare tutti.",
                },
            },
            "required": ["job_id"],
        }

    def execute(self, args: dict) -> str:
        job_id = args.get("job_id", "").strip()
        if not job_id:
            return "ERROR: job_id è obbligatorio."

        if job_id == "all":
            self._manager.cancel_all()
            return "Tutti i job attivi sono stati cancellati."

        ok = self._manager.cancel_job(job_id)
        if not ok:
            return f"ERROR: job non trovato: {job_id}"

        return f"Job {job_id} cancellato. Il worker si fermerà entro 10 secondi."
