from __future__ import annotations
import time
from datetime import datetime
from typing import TYPE_CHECKING
from ..base_tool import BaseTool

if TYPE_CHECKING:
    from src.jobs.job_manager import JobManager


class ScheduleJobTool(BaseTool):
    def __init__(self, manager: "JobManager"):
        self._manager = manager

    def get_name(self): return "schedule_job"

    def get_description(self):
        return (
            "Crea un background job ricorrente che gira a intervallo fisso. "
            "Tipi: time_notification (mostra ora/data), monitor_url (rileva cambiamenti pagina), "
            "web_search_periodic (ricerca web periodica). "
            "Il job continua finché non viene cancellato con cancel_job. "
            "L'output appare come notifica prima del prossimo prompt."
        )

    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["time_notification", "monitor_url", "web_search_periodic"],
                    "description": "Tipo di job",
                },
                "interval_minutes": {
                    "type": "number",
                    "description": "Ogni quanti minuti eseguire il task. Minimo: 1.",
                },
                "description": {
                    "type": "string",
                    "description": "Descrizione leggibile mostrata nelle notifiche. Es. 'News ogni 30 min'.",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Parametri specifici per tipo: "
                        "time_notification: {template: 'Sono le {time} del {date}'} | "
                        "monitor_url: {url: 'https://...'} | "
                        "web_search_periodic: {query: '...', max_results: 5}"
                    ),
                },
                "run_for_minutes": {
                    "type": "number",
                    "description": "Auto-cancella dopo N minuti. Ometti per durata indefinita.",
                },
            },
            "required": ["type", "interval_minutes", "description"],
        }

    def execute(self, args: dict) -> str:
        job_type = args.get("type", "")
        int_min  = max(1.0, float(args.get("interval_minutes", 10)))
        desc     = args.get("description", job_type).strip()
        params   = args.get("params", {}) if isinstance(args.get("params"), dict) else {}
        for_min  = args.get("run_for_minutes")

        if not job_type:
            return "ERROR: type è obbligatorio."
        if not desc:
            return "ERROR: description è obbligatorio."

        run_until = int(time.time() + for_min * 60) if for_min else None

        job_id = self._manager.create_job(
            type=job_type,
            interval_seconds=int(int_min * 60),
            description=desc,
            params=params,
            run_until=run_until,
        )

        until_str = (
            f" (auto-stop alle {datetime.fromtimestamp(run_until).strftime('%H:%M')})"
            if run_until else " (indefinito — usa cancel_job per fermare)"
        )

        return (
            f"Job creato: {job_id}\n"
            f"Tipo: {job_type}\n"
            f"Intervallo: ogni {int_min} min{until_str}\n"
            f"Descrizione: {desc}\n"
            f"Il worker è in esecuzione in background. "
            f"L'output apparirà prima del prossimo prompt."
        )
