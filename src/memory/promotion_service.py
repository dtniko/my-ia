"""PromotionService — promuove voci medium-term → Qdrant prima della scadenza.

Gira come thread daemon ogni `interval` secondi.
Per ogni room che scade entro `promote_window_hours`:
  1. Raccoglie il contenuto (closets + drawers più recenti).
  2. Chiede all'LLM: "è un fatto rilevante a lungo termine o solo contesto di sessione?"
  3. Se sì → salva in Qdrant e segna il room come promosso.
  4. Esegue expire_stale() per rimuovere i room scaduti.
"""
from __future__ import annotations
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from src.ui.cli import CLI


CLASSIFY_PROMPT = """Valuta questa informazione estratta da una sessione di lavoro.

Contenuto:
{content}

Domanda: questo fatto è utile da ricordare OLTRE questa sessione specifica?
Rispondi SOLO con JSON su una riga:
{{"keep": true|false, "reason": "<breve motivazione>", "summary": "<frase sintetica se keep=true, altrimenti stringa vuota>"}}

Esempi di fatti DA TENERE (keep=true):
- preferenze dell'utente (stile, linguaggio, strumenti preferiti)
- nomi e ruoli di persone
- decisioni architetturali ricorrenti
- pattern di lavoro abituali
- fatti sull'identità o contesto dell'utente

Esempi di fatti DA SCARTARE (keep=false):
- output di comandi eseguiti una volta
- stato di una sessione specifica
- errori temporanei risolti
- todo già completati"""


class PromotionService:
    def __init__(
        self,
        medium_term,
        qdrant_memory,
        llm_client,
        llm_model: str,
        interval: int = 1800,        # ogni 30 minuti
        promote_window_hours: int = 12,  # voci che scadono entro 12h
        max_per_cycle: int = 20,
        verbose: bool = False,
    ):
        self.medium = medium_term
        self.qdrant = qdrant_memory
        self.client = llm_client
        self.model = llm_model
        self.interval = max(300, interval)
        self.promote_window_hours = promote_window_hours
        self.max_per_cycle = max_per_cycle
        self.verbose = verbose

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats = {"promoted": 0, "discarded": 0, "cycles": 0}

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="PromotionService"
        )
        self._thread.start()
        if self.verbose:
            CLI.info("PromotionService avviato")

    def stop(self) -> None:
        self._stop_event.set()

    def stats(self) -> dict:
        return dict(self._stats)

    def run_once(self) -> dict:
        """Esegui un ciclo di promozione. Ritorna stats del ciclo."""
        cycle = {"promoted": 0, "discarded": 0, "expired": 0}

        if not (self.medium and self.qdrant and self.qdrant.is_ready()):
            return cycle

        # Trova room che scadranno entro promote_window_hours
        candidates = self._get_near_expiry_rooms()

        for room in candidates[: self.max_per_cycle]:
            content = self._get_room_content(room["id"])
            if not content:
                continue

            keep, summary = self._classify(content)
            if keep and summary:
                try:
                    self.qdrant.add(
                        summary,
                        metadata={
                            "wing": room.get("wing", "general"),
                            "hall": room.get("hall", "facts"),
                            "room": room.get("name", ""),
                            "promoted_from": "medium_term",
                            "promoted_at": datetime.now().isoformat(),
                        },
                    )
                    self._mark_promoted(room["id"])
                    cycle["promoted"] += 1
                    if self.verbose:
                        CLI.info(f"PromotionService: promosso → {summary[:60]}...")
                except Exception as e:
                    CLI.warning(f"PromotionService: errore promozione: {e}")
            else:
                cycle["discarded"] += 1

        # Pulizia scaduti
        try:
            expired = self.medium.expire_stale()
            cycle["expired"] = expired
        except Exception:
            pass

        return cycle

    # ── Privati ───────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        if self._stop_event.wait(min(self.interval, 120)):
            return
        while not self._stop_event.is_set():
            try:
                c = self.run_once()
                self._stats["cycles"] += 1
                self._stats["promoted"] += c["promoted"]
                self._stats["discarded"] += c["discarded"]
            except Exception as e:
                CLI.warning(f"PromotionService: errore ciclo: {e}")
            self._stop_event.wait(self.interval)

    def _get_near_expiry_rooms(self) -> list[dict]:
        """Room che scadranno entro promote_window_hours e non ancora promossi."""
        try:
            conn = sqlite3.connect(self.medium.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cutoff_soon = (
                datetime.now() - timedelta(days=self.medium.ttl_days)
                + timedelta(hours=self.promote_window_hours)
            ).isoformat()
            rows = conn.execute(
                """
                SELECT r.id, r.name, r.access_count, r.last_access,
                       h.name AS hall, w.name AS wing
                FROM rooms r
                JOIN halls h ON h.id = r.hall_id
                JOIN wings w ON w.id = h.wing_id
                WHERE r.last_access < ?
                  AND (r.metadata IS NULL OR r.metadata NOT LIKE '%"promoted":true%')
                ORDER BY r.last_access ASC
                """,
                (cutoff_soon,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _get_room_content(self, room_id: int) -> str:
        """Raccoglie testo rappresentativo del room (closets + ultimi drawers)."""
        try:
            conn = sqlite3.connect(self.medium.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            closets = conn.execute(
                "SELECT content FROM closets WHERE room_id = ? ORDER BY created_at DESC LIMIT 2",
                (room_id,),
            ).fetchall()
            drawers = conn.execute(
                "SELECT content FROM drawers WHERE room_id = ? ORDER BY created_at DESC LIMIT 3",
                (room_id,),
            ).fetchall()
            conn.close()
            parts = [r["content"] for r in closets] + [r["content"] for r in drawers]
            return "\n".join(p for p in parts if p)[:1500]
        except Exception:
            return ""

    def _classify(self, content: str) -> tuple[bool, str]:
        """Chiede all'LLM se il contenuto vale la pena di tenere a lungo termine."""
        try:
            prompt = CLASSIFY_PROMPT.format(content=content[:800])
            msgs = [
                {"role": "system", "content": "Sei un classificatore di memorie. Rispondi solo con JSON."},
                {"role": "user", "content": prompt},
            ]
            raw = self.client.chat(model=self.model, messages=msgs, max_tokens=200)
            raw = raw.strip()
            # estrai JSON anche se il modello aggiunge testo
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return False, ""
            data = json.loads(m.group())
            keep = bool(data.get("keep", False))
            summary = str(data.get("summary", "")).strip()
            return keep, summary
        except Exception:
            return False, ""

    def _mark_promoted(self, room_id: int) -> None:
        """Segna il room come promosso aggiungendo metadata (evita doppia promozione)."""
        try:
            conn = sqlite3.connect(self.medium.db_path, check_same_thread=False)
            # rooms non ha colonna metadata — usiamo last_access come marker
            # alternativa: aggiungiamo una colonna promoted
            try:
                conn.execute("ALTER TABLE rooms ADD COLUMN promoted INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # colonna già esiste
            conn.execute("UPDATE rooms SET promoted = 1 WHERE id = ?", (room_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
