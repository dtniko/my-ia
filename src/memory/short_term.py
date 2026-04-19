"""
ShortTermMemory — memoria di lavoro per-scope (brevissimo termine).

Segue la metafora MemPalace adattata per la RAM:
  - drawers : ultimi N turni verbatim (user/assistant/tool_result)
  - closet  : riassunto live, aggiornato ogni K drawer scritti

Vive in memoria. Ogni sessione / task / job ha il suo scope nominato.
Il dump opzionale su disco serve ai job ciclici (`~/.ltsia/scopes/<name>.json`).

Non usa embedding: lookup è lineare con match sul contenuto dei drawer e
full-text sul closet. È pensata per <200 elementi per scope.
"""
from __future__ import annotations
import json
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable, Optional


class ShortTermScope:
    """Uno scope di short-term (es. sessione corrente, singolo job ciclico)."""

    def __init__(self, name: str, max_drawers: int = 20):
        self.name = name
        self.max_drawers = max_drawers
        self.drawers: deque[dict] = deque(maxlen=max_drawers)
        self.closet: str = ""
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self._drawers_since_closet = 0
        self._lock = Lock()

    # ── Write ────────────────────────────────────────────────────────────────

    def add_drawer(self, role: str, content: str, metadata: Optional[dict] = None) -> None:
        with self._lock:
            self.drawers.append({
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "at": datetime.now().isoformat(),
            })
            self._drawers_since_closet += 1
            self.updated_at = datetime.now().isoformat()

    def set_closet(self, summary: str) -> None:
        with self._lock:
            self.closet = summary
            self._drawers_since_closet = 0
            self.updated_at = datetime.now().isoformat()

    def maybe_update_closet(
        self,
        summarizer: Callable[[list[dict], str], str],
        every: int = 5,
    ) -> bool:
        """Se si sono accumulati `every` drawer nuovi, rigenera il closet via callback."""
        if self._drawers_since_closet < every:
            return False
        try:
            new_summary = summarizer(list(self.drawers), self.closet)
            if new_summary:
                self.set_closet(new_summary)
                return True
        except Exception:
            pass
        return False

    # ── Read ─────────────────────────────────────────────────────────────────

    def recent_drawers(self, n: int = 10) -> list[dict]:
        with self._lock:
            return list(self.drawers)[-n:]

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Lookup naive: match case-insensitive sul contenuto dei drawer."""
        q = query.lower().strip()
        if not q:
            return list(self.drawers)[-limit:]
        hits = []
        for d in self.drawers:
            content = str(d.get("content", ""))
            if q in content.lower():
                hits.append({
                    "tier": "short",
                    "scope": self.name,
                    "role": d["role"],
                    "content": content,
                    "at": d["at"],
                })
        return hits[-limit:]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closet": self.closet,
            "drawers": list(self.drawers),
        }

    @classmethod
    def from_dict(cls, data: dict, max_drawers: int = 20) -> "ShortTermScope":
        s = cls(data.get("name", "default"), max_drawers=max_drawers)
        s.created_at = data.get("created_at", s.created_at)
        s.updated_at = data.get("updated_at", s.updated_at)
        s.closet = data.get("closet", "")
        for d in data.get("drawers", [])[-max_drawers:]:
            s.drawers.append(d)
        return s


class ShortTermMemory:
    """Gestore di più scope di short-term."""

    def __init__(self, max_drawers: int = 20, persist_dir: Optional[str] = None):
        self.max_drawers = max_drawers
        self.persist_dir = Path(persist_dir) if persist_dir else None
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._scopes: dict[str, ShortTermScope] = {}
        self._lock = Lock()

    def scope(self, name: str, persist: bool = False) -> ShortTermScope:
        """Ottieni (o crea) uno scope. Se persist=True e esiste su disco, lo ricarica."""
        with self._lock:
            if name in self._scopes:
                return self._scopes[name]
            if persist and self.persist_dir:
                file = self.persist_dir / f"{name}.json"
                if file.exists():
                    try:
                        data = json.loads(file.read_text())
                        s = ShortTermScope.from_dict(data, self.max_drawers)
                        self._scopes[name] = s
                        return s
                    except Exception:
                        pass
            s = ShortTermScope(name, self.max_drawers)
            self._scopes[name] = s
            return s

    def persist(self, name: str) -> bool:
        """Salva su disco uno scope (per job ciclici)."""
        if not self.persist_dir:
            return False
        scope = self._scopes.get(name)
        if not scope:
            return False
        try:
            (self.persist_dir / f"{name}.json").write_text(
                json.dumps(scope.as_dict(), indent=2, ensure_ascii=False),
            )
            return True
        except Exception:
            return False

    def drop(self, name: str) -> None:
        with self._lock:
            self._scopes.pop(name, None)
            if self.persist_dir:
                try:
                    (self.persist_dir / f"{name}.json").unlink(missing_ok=True)
                except Exception:
                    pass

    def recall_all(self, query: str, limit_per_scope: int = 5) -> list[dict]:
        """Cerca in tutti gli scope attivi. Utile quando non si sa quale è rilevante."""
        results = []
        for scope in self._scopes.values():
            results.extend(scope.recall(query, limit_per_scope))
        return results

    def list_scopes(self) -> list[str]:
        return list(self._scopes.keys())
