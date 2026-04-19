"""Job — value object per un background job ricorrente."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    id:               str
    type:             str
    description:      str
    params:           dict
    interval_seconds: int
    created_at:       int
    run_until:        Optional[int]
    status:           str          # active | cancelled | crashed
    run_count:        int = 0

    # ── Factory ───────────────────────────────────────────────────────────────

    @staticmethod
    def from_dict(data: dict) -> "Job":
        return Job(
            id=data["id"],
            type=data["type"],
            description=data.get("description", ""),
            params=data.get("params", {}),
            interval_seconds=max(10, int(data.get("interval_seconds", 60))),
            created_at=int(data.get("created_at", time.time())),
            run_until=int(data["run_until"]) if data.get("run_until") is not None else None,
            status=data.get("status", "active"),
            run_count=int(data.get("run_count", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "type":             self.type,
            "description":      self.description,
            "params":           self.params,
            "interval_seconds": self.interval_seconds,
            "created_at":       self.created_at,
            "run_until":        self.run_until,
            "status":           self.status,
            "run_count":        self.run_count,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        return self.status == "active"

    def is_expired(self) -> bool:
        return self.run_until is not None and time.time() > self.run_until
