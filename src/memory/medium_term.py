"""
MediumTermMemory — memoria a medio termine con gerarchia MemPalace.

Storage: SQLite con FTS5 per full-text search (no embedding).
Schema:
  wings  : aree di alto livello (es. "progetto:ltsia", "utente:roque")
  halls  : categorie dentro una wing ("preferences", "events", "facts", ...)
  rooms  : argomenti specifici dentro un hall
  closets: riassunti compressi di un room (uno o più)
  drawers: originali verbatim (conversazioni, osservazioni, output)

Politica di retention:
  - TTL configurabile (default 30 giorni): i room non toccati da N giorni vengono
    marcati come "stale" e possono essere promossi a long-term o cancellati.
  - Ogni lookup aggiorna last_access e incrementa access_count.
  - Promote candidates: room con access_count >= promote_threshold.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Optional


class MediumTermMemory:
    def __init__(self, db_path: str, ttl_days: int = 30, promote_threshold: int = 3):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.promote_threshold = promote_threshold
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = Lock()
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        c = self._db()
        c.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS wings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS halls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wing_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(wing_id, name),
            FOREIGN KEY(wing_id) REFERENCES wings(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hall_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            summary TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            last_access TEXT NOT NULL,
            access_count INTEGER DEFAULT 0,
            UNIQUE(hall_id, name),
            FOREIGN KEY(hall_id) REFERENCES halls(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS closets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS drawers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_halls_wing ON halls(wing_id);
        CREATE INDEX IF NOT EXISTS idx_rooms_hall ON rooms(hall_id);
        CREATE INDEX IF NOT EXISTS idx_rooms_access ON rooms(last_access);
        CREATE INDEX IF NOT EXISTS idx_closets_room ON closets(room_id);
        CREATE INDEX IF NOT EXISTS idx_drawers_room ON drawers(room_id);
        """)

        # FTS5 virtual table su drawers.content + closets.content
        c.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
            content,
            source_type UNINDEXED,
            source_id UNINDEXED,
            room_id UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        """)
        c.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def remember(
        self,
        wing: str,
        hall: str,
        room: str,
        content: str,
        kind: str = "drawer",  # "drawer" | "closet"
        metadata: Optional[dict] = None,
    ) -> dict:
        """Salva un'informazione nella gerarchia. Crea wing/hall/room se mancano."""
        with self._lock:
            c = self._db()
            now = datetime.now().isoformat()

            # Upsert wing
            c.execute("INSERT OR IGNORE INTO wings(name, created_at) VALUES (?, ?)", (wing, now))
            wing_id = c.execute("SELECT id FROM wings WHERE name = ?", (wing,)).fetchone()["id"]

            # Upsert hall
            c.execute(
                "INSERT OR IGNORE INTO halls(wing_id, name, created_at) VALUES (?, ?, ?)",
                (wing_id, hall, now),
            )
            hall_id = c.execute(
                "SELECT id FROM halls WHERE wing_id = ? AND name = ?", (wing_id, hall),
            ).fetchone()["id"]

            # Upsert room
            c.execute(
                """INSERT OR IGNORE INTO rooms(hall_id, name, created_at, last_access, access_count)
                   VALUES (?, ?, ?, ?, 0)""",
                (hall_id, room, now, now),
            )
            room_row = c.execute(
                "SELECT id FROM rooms WHERE hall_id = ? AND name = ?", (hall_id, room),
            ).fetchone()
            room_id = room_row["id"]

            # Insert drawer o closet
            if kind == "closet":
                cur = c.execute(
                    "INSERT INTO closets(room_id, content, created_at) VALUES (?, ?, ?)",
                    (room_id, content, now),
                )
                source_id = cur.lastrowid
                source_type = "closet"
                # aggiorna anche room.summary all'ultimo closet
                c.execute("UPDATE rooms SET summary = ? WHERE id = ?", (content, room_id))
            else:
                cur = c.execute(
                    "INSERT INTO drawers(room_id, content, metadata, created_at) VALUES (?, ?, ?, ?)",
                    (room_id, content, json.dumps(metadata or {}, ensure_ascii=False), now),
                )
                source_id = cur.lastrowid
                source_type = "drawer"

            # FTS index
            c.execute(
                "INSERT INTO mem_fts(content, source_type, source_id, room_id) VALUES (?, ?, ?, ?)",
                (content, source_type, source_id, room_id),
            )

            c.commit()
            return {
                "wing_id": wing_id,
                "hall_id": hall_id,
                "room_id": room_id,
                "source_id": source_id,
                "source_type": source_type,
            }

    # ── Search ────────────────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        limit: int = 10,
        wing: Optional[str] = None,
    ) -> list[dict]:
        """Full-text search via FTS5 sul contenuto. Ritorna hits con gerarchia completa."""
        with self._lock:
            c = self._db()
            q = _fts_escape(query)
            if not q:
                return []

            try:
                if wing:
                    rows = c.execute(
                        """
                        SELECT
                            f.content        AS content,
                            f.source_type    AS source_type,
                            f.source_id      AS source_id,
                            r.id             AS room_id,
                            r.name           AS room_name,
                            h.name           AS hall_name,
                            w.name           AS wing_name,
                            bm25(mem_fts)    AS score
                        FROM mem_fts f
                        JOIN rooms r ON r.id = f.room_id
                        JOIN halls h ON h.id = r.hall_id
                        JOIN wings w ON w.id = h.wing_id
                        WHERE mem_fts MATCH ? AND w.name = ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (q, wing, limit),
                    ).fetchall()
                else:
                    rows = c.execute(
                        """
                        SELECT
                            f.content        AS content,
                            f.source_type    AS source_type,
                            f.source_id      AS source_id,
                            r.id             AS room_id,
                            r.name           AS room_name,
                            h.name           AS hall_name,
                            w.name           AS wing_name,
                            bm25(mem_fts)    AS score
                        FROM mem_fts f
                        JOIN rooms r ON r.id = f.room_id
                        JOIN halls h ON h.id = r.hall_id
                        JOIN wings w ON w.id = h.wing_id
                        WHERE mem_fts MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (q, limit),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

            # bm25: score più basso = più rilevante. Normalizzo a 0..1 invertito.
            results: list[dict] = []
            touched_rooms = set()
            for r in rows:
                raw = r["score"] or 0.0
                norm = 1.0 / (1.0 + abs(raw)) if raw else 0.0
                results.append({
                    "tier": "medium",
                    "wing": r["wing_name"],
                    "hall": r["hall_name"],
                    "room": r["room_name"],
                    "source_type": r["source_type"],
                    "content": r["content"],
                    "score": norm,
                })
                touched_rooms.add(r["room_id"])

            if touched_rooms:
                self._touch_rooms(list(touched_rooms))
            return results

    def _touch_rooms(self, room_ids: list[int]) -> None:
        now = datetime.now().isoformat()
        c = self._db()
        for rid in room_ids:
            c.execute(
                "UPDATE rooms SET last_access = ?, access_count = access_count + 1 WHERE id = ?",
                (now, rid),
            )
        c.commit()

    # ── Retention ─────────────────────────────────────────────────────────────

    def promote_candidates(self) -> list[dict]:
        """Room con access_count >= threshold, candidati a long-term."""
        c = self._db()
        rows = c.execute(
            """
            SELECT r.id, r.name, r.summary, r.access_count, h.name AS hall, w.name AS wing
            FROM rooms r
            JOIN halls h ON h.id = r.hall_id
            JOIN wings w ON w.id = h.wing_id
            WHERE r.access_count >= ?
            """,
            (self.promote_threshold,),
        ).fetchall()
        return [dict(r) for r in rows]

    def expire_stale(self) -> int:
        """Rimuove i room non toccati da più di ttl_days. Ritorna count."""
        cutoff = (datetime.now() - timedelta(days=self.ttl_days)).isoformat()
        c = self._db()
        rows = c.execute(
            "SELECT id FROM rooms WHERE last_access < ? AND access_count < ?",
            (cutoff, self.promote_threshold),
        ).fetchall()
        ids = [r["id"] for r in rows]
        for rid in ids:
            c.execute("DELETE FROM closets WHERE room_id = ?", (rid,))
            c.execute("DELETE FROM drawers WHERE room_id = ?", (rid,))
            c.execute("DELETE FROM mem_fts WHERE room_id = ?", (rid,))
            c.execute("DELETE FROM rooms WHERE id = ?", (rid,))
        c.commit()
        return len(ids)

    def stats(self) -> dict:
        c = self._db()
        return {
            "wings": c.execute("SELECT COUNT(*) FROM wings").fetchone()[0],
            "halls": c.execute("SELECT COUNT(*) FROM halls").fetchone()[0],
            "rooms": c.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
            "closets": c.execute("SELECT COUNT(*) FROM closets").fetchone()[0],
            "drawers": c.execute("SELECT COUNT(*) FROM drawers").fetchone()[0],
        }


def _fts_escape(q: str) -> str:
    """Escape query per FTS5: rimuove caratteri problematici e quota i token."""
    import re
    tokens = re.findall(r"\w+", q, flags=re.UNICODE)
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens if len(t) >= 2)
