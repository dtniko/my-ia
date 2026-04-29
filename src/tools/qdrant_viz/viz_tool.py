"""
QdrantVizTool — interfaccia web 3D per esplorare la memoria Qdrant.

La visualizzazione è integrata nel server HTTP principale (porta 8080) alla
rotta /viz. Le API fetch/delete passano via WebSocket invece di un server HTTP
separato — così funziona correttamente anche da remoto.

Tool: qdrant_viz
  action='status' → stato corrente (il viz è sempre attivo se Qdrant è pronto)
"""
from __future__ import annotations
import math
import random
from typing import Optional

import requests

from ..base_tool import BaseTool


def _ws_html(ws_port: int = 8765) -> str:
    """HTML della pagina viz che usa WebSocket per fetch/delete."""
    return r"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Qdrant Viz · LTSIA</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    html, body { margin: 0; padding: 0; height: 100%; background: #0b0d10; color: #e6e9ef; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    header { display: flex; align-items: center; gap: 16px; padding: 10px 18px; background: #12161c; border-bottom: 1px solid #222; }
    header h1 { margin: 0; font-size: 14px; font-weight: 500; }
    header code { background: #1b2029; padding: 2px 6px; border-radius: 3px; font-size: 12px; }
    header .count { color: #8aa4c7; font-size: 12px; }
    header .spacer { flex: 1; }
    button { background: #1e252e; color: #e6e9ef; border: 1px solid #333; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
    button:hover { background: #2a333f; }
    button.danger { background: #5a1c1c; border-color: #7a2a2a; }
    button.danger:hover { background: #7a2a2a; }
    #plot { width: 100vw; height: calc(100vh - 46px); }
    #info { position: fixed; bottom: 16px; left: 16px; background: rgba(18, 22, 28, 0.95); border: 1px solid #333; padding: 12px 14px; max-width: 420px; border-radius: 6px; display: none; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
    #info .content { font-size: 13px; white-space: pre-wrap; line-height: 1.4; max-height: 220px; overflow: auto; }
    #info .meta { font-size: 11px; color: #7a8797; margin-top: 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    #info .actions { margin-top: 12px; display: flex; gap: 8px; }
    #status { position: fixed; top: 60px; right: 16px; padding: 8px 12px; background: rgba(18,22,28,0.95); border: 1px solid #333; border-radius: 4px; font-size: 12px; display: none; }
    #ws-state { position: fixed; top: 10px; right: 16px; font-size: 11px; color: #7a8797; }
  </style>
</head>
<body>
  <header>
    <h1>Qdrant Viz — <code id="coll">…</code></h1>
    <span class="count" id="count"></span>
    <span class="spacer"></span>
    <button id="refresh">Ricarica</button>
  </header>
  <div id="plot"></div>
  <div id="info">
    <div class="content" id="info-content"></div>
    <div class="meta" id="info-meta"></div>
    <div class="actions">
      <button id="close-info">Chiudi</button>
      <button class="danger" id="delete-btn">Elimina punto</button>
    </div>
  </div>
  <div id="status"></div>
  <div id="ws-state">WS: connessione…</div>

<script>
const WS_PORT = """ + str(ws_port) + r""";
const WS_URL  = `ws://${window.location.hostname}:${WS_PORT}`;

let currentId = null;
let cache = [];
let ws = null;
let pendingFetch = false;
let pendingDelete = null;

function flash(msg, ok = true) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.style.borderColor = ok ? '#2a6a3a' : '#7a2a2a';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 2500);
}

function setWsState(label) {
  document.getElementById('ws-state').textContent = 'WS: ' + label;
}

function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    setWsState('connesso');
    load();
  };
  ws.onclose = () => {
    setWsState('disconnesso — riconnetto…');
    setTimeout(connect, 3000);
  };
  ws.onerror = () => setWsState('errore');
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'qdrant_points')        handlePoints(msg);
    if (msg.type === 'qdrant_delete_result') handleDeleteResult(msg);
  };
}

function load() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  flash('Caricamento…');
  pendingFetch = true;
  ws.send(JSON.stringify({ type: 'qdrant_fetch' }));
}

function handlePoints(msg) {
  pendingFetch = false;
  cache = msg.points || [];
  document.getElementById('coll').textContent  = msg.collection || '';
  document.getElementById('count').textContent = cache.length + ' punti';

  const byHall = {};
  for (const p of cache) {
    const k = p.hall || 'other';
    (byHall[k] = byHall[k] || []).push(p);
  }

  const traces = Object.entries(byHall).map(([hall, pts]) => ({
    type: 'scatter3d',
    mode: 'markers',
    name: hall,
    x: pts.map(p => p.x),
    y: pts.map(p => p.y),
    z: pts.map(p => p.z),
    text: pts.map(p => {
      const c = (p.content || '').replace(/\s+/g, ' ');
      return c.length > 90 ? c.slice(0, 90) + '…' : c;
    }),
    customdata: pts.map(p => p.id),
    hovertemplate: '%{text}<extra>' + hall + '</extra>',
    marker: { size: 5, opacity: 0.85, line: { width: 0 } },
  }));

  const layout = {
    paper_bgcolor: '#0b0d10',
    plot_bgcolor:  '#0b0d10',
    font: { color: '#e6e9ef' },
    scene: {
      xaxis: { color: '#556', gridcolor: '#1e2430', backgroundcolor: '#0b0d10', showbackground: true },
      yaxis: { color: '#556', gridcolor: '#1e2430', backgroundcolor: '#0b0d10', showbackground: true },
      zaxis: { color: '#556', gridcolor: '#1e2430', backgroundcolor: '#0b0d10', showbackground: true },
    },
    legend: { bgcolor: 'rgba(0,0,0,0)', font: { size: 11 } },
    margin: { l: 0, r: 0, t: 0, b: 0 },
  };

  Plotly.newPlot('plot', traces, layout, { responsive: true, displaylogo: false });

  document.getElementById('plot').on('plotly_click', (e) => {
    const p  = e.points[0];
    const id = p.customdata;
    const pt = cache.find(x => x.id === id);
    if (!pt) return;
    currentId = id;
    document.getElementById('info-content').textContent = pt.content || '(vuoto)';
    document.getElementById('info-meta').textContent =
      'id: ' + id + '\n' +
      'wing: ' + (pt.wing || '—') + '  ·  hall: ' + (pt.hall || '—') + '  ·  room: ' + (pt.room || '—') + '\n' +
      'created: ' + (pt.created_at || '—');
    document.getElementById('info').style.display = 'block';
  });

  flash('Caricati ' + cache.length + ' punti');
}

function handleDeleteResult(msg) {
  if (msg.ok) {
    flash('Punto eliminato');
    document.getElementById('info').style.display = 'none';
    currentId = null;
    load();
  } else {
    flash('Eliminazione fallita', false);
  }
}

document.getElementById('refresh').onclick = load;

document.getElementById('close-info').onclick = () => {
  document.getElementById('info').style.display = 'none';
  currentId = null;
};

document.getElementById('delete-btn').onclick = () => {
  if (!currentId) return;
  if (!confirm('Eliminare definitivamente questo punto?')) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    flash('WebSocket non connesso', false);
    return;
  }
  ws.send(JSON.stringify({ type: 'qdrant_delete', id: currentId }));
};

connect();
</script>
</body>
</html>
"""


class _VizBackend:
    """Backend HTTP REST — usato con qdrant_mode=server."""

    def __init__(self, qdrant_url: str, collection: str):
        self.qdrant_url = qdrant_url.rstrip("/")
        self.collection = collection

    def fetch_points(self) -> dict:
        url = f"{self.qdrant_url}/collections/{self.collection}/points/scroll"
        all_points: list[dict] = []
        offset = None
        while True:
            body = {"limit": 500, "with_payload": True, "with_vector": True}
            if offset is not None:
                body["offset"] = offset
            r = requests.post(url, json=body, timeout=30)
            r.raise_for_status()
            res = r.json().get("result", {})
            all_points.extend(res.get("points", []) or [])
            offset = res.get("next_page_offset")
            if offset is None:
                break
        return _build_points_result(self.collection, all_points)

    def delete_point(self, pid: str) -> bool:
        url = f"{self.qdrant_url}/collections/{self.collection}/points/delete"
        payload_ids: list = [pid]
        try:
            payload_ids = [int(pid)] if pid.isdigit() else [pid]
        except Exception:
            pass
        r = requests.post(url, json={"points": payload_ids}, timeout=15)
        return r.ok


class _LocalVizBackend:
    """Backend qdrant-client embedded — riusa il client già aperto da QdrantMemory."""

    def __init__(self, client, collection: str):
        self._client = client
        self.collection = collection

    def fetch_points(self) -> dict:
        client = self._client
        all_points = []
        offset = None
        while True:
            res = client.scroll(
                collection_name=self.collection,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            records, offset = res
            for r in records:
                vec = r.vector
                if isinstance(vec, dict):
                    vec = next(iter(vec.values()), None)
                payload = r.payload or {}
                all_points.append({
                    "id": str(r.id),
                    "vector": vec,
                    "payload": payload,
                })
            if offset is None:
                break
        return _build_points_result(self.collection, all_points)

    def delete_point(self, pid: str) -> bool:
        from qdrant_client.models import PointIdsList
        client = self._client
        try:
            point_id = int(pid) if pid.isdigit() else pid
            client.delete(
                collection_name=self.collection,
                points_selector=PointIdsList(points=[point_id]),
            )
            return True
        except Exception:
            return False


def build_backend(config, qdrant_memory=None):
    """
    Crea e ritorna il backend corretto (locale o server) senza avviare
    nessun HTTP server. Usato da VoiceServer per gestire i messaggi WS.
    Ritorna None se Qdrant non è configurato.
    """
    if not config:
        return None
    coll = getattr(config, "qdrant_collection", None)
    if not coll:
        return None
    mode = getattr(config, "qdrant_mode", "server")
    if mode == "local":
        client = getattr(qdrant_memory, "_client", None) if qdrant_memory else None
        if client is None:
            return None
        return _LocalVizBackend(client, coll)
    else:
        qurl = getattr(config, "qdrant_url", None)
        if not qurl:
            return None
        return _VizBackend(qurl, coll)


def _build_points_result(collection: str, raw_points: list[dict]) -> dict:
    vectors: list[list[float]] = []
    meta: list[dict] = []
    for p in raw_points:
        v = p.get("vector")
        if isinstance(v, dict):
            v = next(iter(v.values()), None)
        if not v:
            continue
        vectors.append(v)
        payload = p.get("payload") or {}
        meta.append({
            "id": p.get("id"),
            "content": payload.get("content", ""),
            "wing": payload.get("wing", ""),
            "hall": payload.get("hall", ""),
            "room": payload.get("room", ""),
            "created_at": payload.get("created_at", ""),
        })
    coords = _project_3d(vectors)
    points = []
    for m, c in zip(meta, coords):
        m["x"], m["y"], m["z"] = c[0], c[1], c[2]
        points.append(m)
    return {"collection": collection, "points": points}


def _project_3d(vectors: list[list[float]]) -> list[list[float]]:
    if not vectors:
        return []
    try:
        import numpy as np
        X = np.asarray(vectors, dtype=float)
        X = X - X.mean(axis=0, keepdims=True)
        _, _, Vt = np.linalg.svd(X, full_matrices=False)
        return (X @ Vt[:3].T).tolist()
    except Exception:
        return _random_projection_3d(vectors)


def _random_projection_3d(vectors: list[list[float]], seed: int = 42) -> list[list[float]]:
    d = len(vectors[0])
    rng = random.Random(seed)
    axes = []
    for _ in range(3):
        v = [rng.gauss(0.0, 1.0) for _ in range(d)]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        axes.append([x / n for x in v])
    out = []
    for v in vectors:
        out.append([sum(axes[k][i] * v[i] for i in range(d)) for k in range(3)])
    return out


class QdrantVizTool(BaseTool):
    """
    Tool qdrant_viz: informa l'agente che la viz è disponibile alla rotta /viz
    del server HTTP principale. Non avvia più un server separato.
    """

    def __init__(self, config=None, qdrant_memory=None):
        self.config = config
        self.qdrant_memory = qdrant_memory

    def get_name(self) -> str:
        return "qdrant_viz"

    def get_description(self) -> str:
        return (
            "Interfaccia web 3D dei punti in Qdrant: pallini con label del "
            "content, zoom, rotazione, eliminazione singoli punti. "
            "action: 'status' → ritorna l'URL della viz integrata nel server principale."
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status"],
                    "description": "status: ritorna URL della viz (default)",
                },
            },
        }

    def execute(self, args: dict) -> str:
        coll = getattr(self.config, "qdrant_collection", None) if self.config else None
        if not coll:
            return "Qdrant Viz non disponibile (qdrant_collection non configurato)"
        return (
            f"Qdrant Viz disponibile su http://<host>:8080/viz "
            f"(collection '{coll}') — API via WebSocket integrato"
        )
