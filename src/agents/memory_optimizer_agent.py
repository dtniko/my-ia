"""
MemoryOptimizerAgent — manutenzione periodica della memoria Qdrant.

Ogni `interval` secondi scansiona un batch di punti e prova a:
  - MERGE: coppie molto simili → fonde in un unico punto (auto se score >= auto_merge,
           chiede all'LLM nella fascia ambigua [merge, auto_merge))
  - SPLIT: punti con content lungo → chiede all'LLM se contengono più fatti atomici
           indipendenti, in tal caso li separa in più punti.

Lo scan usa un cursore incrementale su Qdrant; quando finisce ricomincia dall'inizio.
Il loop gira in un thread daemon — `stop()` lo termina al più presto.
"""
from __future__ import annotations
import json
import re
import threading
from typing import Optional

from src.http.llm_client import LlmClientInterface
from src.memory.qdrant_memory import QdrantMemory
from src.ui.cli import CLI


MERGE_PROMPT = """Hai due memorie semanticamente simili.
Decidi se descrivono LO STESSO fatto (MERGE) o due fatti diversi che si somigliano (KEEP).

A: {a}
B: {b}

Rispondi SOLO con JSON su una riga:
{{"decision": "merge"|"keep", "merged_content": "<testo sintetizzato se merge, altrimenti stringa vuota>"}}
Se merge, merged_content deve contenere entrambe le informazioni senza perdite, in una sola frase chiara."""

SPLIT_PROMPT = """Questa memoria potrebbe contenere più fatti atomici indipendenti.
Un "fatto atomico" è un'informazione auto-contenuta che ha senso da sola.

Memoria: {content}

Rispondi SOLO con JSON su una riga:
{{"decision": "keep"|"split", "parts": ["<fatto1>", "<fatto2>", ...]}}
Se keep, parts = []. Se split, parts contiene 2+ fatti indipendenti (ciascuno una frase chiara)."""


class MemoryOptimizerAgent:
    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        qdrant_memory: QdrantMemory,
        interval: int = 900,
        batch_size: int = 30,
        merge_threshold: float = 0.87,
        auto_merge_threshold: float = 0.97,
        split_min_chars: int = 120,
        verbose: bool = False,
    ):
        self.client = client
        self.model = model
        self.qdrant = qdrant_memory
        self.interval = max(60, int(interval))
        self.batch_size = max(5, int(batch_size))
        self.merge_threshold = merge_threshold
        self.auto_merge_threshold = auto_merge_threshold
        self.split_min_chars = split_min_chars
        self.verbose = verbose

        self._cursor = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats_total = {"merged": 0, "split": 0, "cycles": 0}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MemoryOptimizer")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def stats(self) -> dict:
        return dict(self._stats_total)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Primo wait prima del primo ciclo, così l'avvio di LTSIA resta reattivo
        if self._stop_event.wait(self.interval):
            return
        while not self._stop_event.is_set():
            try:
                s = self.run_once()
                self._stats_total["cycles"] += 1
                self._stats_total["merged"] += s.get("merged", 0)
                self._stats_total["split"] += s.get("split", 0)
                if self.verbose and (s.get("merged") or s.get("split")):
                    CLI.info(
                        f"[memory-optimizer] esaminati {s['examined']} · "
                        f"merged {s['merged']} · split {s['split']}"
                    )
            except Exception as e:
                if self.verbose:
                    CLI.warning(f"[memory-optimizer] errore ciclo: {e}")
            if self._stop_event.wait(self.interval):
                return

    # ── Single cycle ──────────────────────────────────────────────────────────

    def run_once(self) -> dict:
        """Esegue un singolo ciclo di ottimizzazione su un batch di punti."""
        if not self.qdrant.is_ready():
            return {"examined": 0, "merged": 0, "split": 0}

        points, next_offset = self.qdrant.scroll(limit=self.batch_size, offset=self._cursor)
        self._cursor = next_offset  # se None alla fine, riparte da capo al prossimo ciclo

        if not points:
            return {"examined": 0, "merged": 0, "split": 0}

        processed: set[str] = set()
        merged_count = 0
        split_count = 0

        # ── Pass 1: merge ──
        for p in points:
            if self._stop_event.is_set():
                break
            pid = p["id"]
            if pid in processed:
                continue
            neighbors = self.qdrant.find_similar(p["vector"], limit=3, exclude_id=pid)
            for n in neighbors:
                nid = n["id"]
                if nid in processed:
                    continue
                score = n["score"]
                if score < self.merge_threshold:
                    continue
                # Auto-merge se altissima similarità, altrimenti chiedi al LLM
                should_merge = score >= self.auto_merge_threshold
                merged_content = ""
                if not should_merge:
                    decision = self._ask_merge(p["payload"].get("content", ""), n["content"])
                    should_merge = decision.get("decision") == "merge"
                    merged_content = (decision.get("merged_content") or "").strip()
                if should_merge:
                    if not merged_content:
                        merged_content = self._fallback_merge(
                            p["payload"].get("content", ""),
                            n["content"],
                        )
                    ok = self._apply_merge(p, {"id": nid, "payload": n["payload"]}, merged_content)
                    if ok:
                        merged_count += 1
                        processed.add(pid)
                        processed.add(nid)
                        break  # p è stato consumato, passa al successivo

        # ── Pass 2: split ──
        for p in points:
            if self._stop_event.is_set():
                break
            pid = p["id"]
            if pid in processed:
                continue
            content = p["payload"].get("content", "") or ""
            if len(content) < self.split_min_chars:
                continue
            decision = self._ask_split(content)
            if decision.get("decision") != "split":
                continue
            parts = [str(x).strip() for x in (decision.get("parts") or []) if str(x).strip()]
            if len(parts) < 2:
                continue
            ok = self._apply_split(p, parts)
            if ok:
                split_count += 1
                processed.add(pid)

        return {"examined": len(points), "merged": merged_count, "split": split_count}

    # ── LLM calls ────────────────────────────────────────────────────────────

    def _ask_merge(self, a: str, b: str) -> dict:
        messages = [
            {"role": "system", "content": "Rispondi SOLO con JSON valido."},
            {"role": "user", "content": MERGE_PROMPT.format(a=a, b=b)},
        ]
        try:
            resp = self.client.chat(model=self.model, messages=messages)
            return _parse_json_object(resp.get("message", {}).get("content", ""))
        except Exception:
            return {}

    def _ask_split(self, content: str) -> dict:
        messages = [
            {"role": "system", "content": "Rispondi SOLO con JSON valido."},
            {"role": "user", "content": SPLIT_PROMPT.format(content=content)},
        ]
        try:
            resp = self.client.chat(model=self.model, messages=messages)
            return _parse_json_object(resp.get("message", {}).get("content", ""))
        except Exception:
            return {}

    # ── Mutations ─────────────────────────────────────────────────────────────

    def _apply_merge(self, a: dict, b: dict, merged_content: str) -> bool:
        pa, pb = a["payload"], b["payload"]
        overrides = {
            "wing": pa.get("wing") or pb.get("wing") or "default",
            "hall": pa.get("hall") or pb.get("hall") or "facts",
            "room": pa.get("room") or pb.get("room") or "",
            "metadata": {**(pb.get("metadata") or {}), **(pa.get("metadata") or {})},
            "created_at": _min_iso(pa.get("created_at"), pb.get("created_at")),
            "last_access": _max_iso(pa.get("last_access"), pb.get("last_access")),
            "access_count": int(pa.get("access_count", 0)) + int(pb.get("access_count", 0)) + 1,
        }
        new_id = self.qdrant.upsert_raw(merged_content, overrides)
        if not new_id:
            return False
        self.qdrant.delete(a["id"])
        self.qdrant.delete(b["id"])
        return True

    def _apply_split(self, p: dict, parts: list[str]) -> bool:
        payload = p["payload"]
        base_overrides = {
            "wing": payload.get("wing", "default"),
            "hall": payload.get("hall", "facts"),
            "room": payload.get("room", ""),
            "metadata": payload.get("metadata") or {},
            "created_at": payload.get("created_at"),
            "access_count": int(payload.get("access_count", 0)),
        }
        created_ids = []
        for part in parts:
            nid = self.qdrant.upsert_raw(part, dict(base_overrides))
            if nid:
                created_ids.append(nid)
        if not created_ids:
            return False
        self.qdrant.delete(p["id"])
        return True

    @staticmethod
    def _fallback_merge(a: str, b: str) -> str:
        """Merge testuale semplice se l'LLM non fornisce merged_content."""
        a, b = a.strip(), b.strip()
        if a == b:
            return a
        return a if len(a) >= len(b) else b


# ── Utilities ─────────────────────────────────────────────────────────────────

def _parse_json_object(text: str) -> dict:
    """Estrae il primo JSON object dal testo (robusto a preamboli LLM)."""
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _min_iso(a: Optional[str], b: Optional[str]) -> str:
    vals = [x for x in (a, b) if x]
    return min(vals) if vals else ""


def _max_iso(a: Optional[str], b: Optional[str]) -> str:
    vals = [x for x in (a, b) if x]
    return max(vals) if vals else ""
