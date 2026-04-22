"""
QdrantVizTool — interfaccia web 3D per esplorare la memoria Qdrant.

Avvia un server HTTP locale che proietta in 3D (PCA se numpy disponibile,
random projection altrimenti) tutti i punti della collection e li mostra
in un grafico Plotly con zoom, rotazione, hover sul content, click per
vedere il dettaglio ed eliminare il punto.

Tool: qdrant_viz
  action='start'  → avvia il server (default porta 8090)
  action='stop'   → ferma il server
  action='status' → stato corrente
"""
from __future__ import annotations
import json
import math
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

from ..base_tool import BaseTool


HTML_PAGE = r"""<!DOCTYPE html>
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

<script>
let currentId = null;
let cache = [];

function flash(msg, ok = true) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.style.borderColor = ok ? '#2a6a3a' : '#7a2a2a';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 2500);
}

async function load() {
  flash('Caricamento…');
  let data;
  try {
    const r = await fetch('/api/points');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (e) {
    flash('Errore: ' + e.message, false);
    return;
  }
  cache = data.points || [];
  document.getElementById('coll').textContent = data.collection || '';
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
    plot_bgcolor: '#0b0d10',
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

  const plotEl = document.getElementById('plot');
  plotEl.on('plotly_click', (e) => {
    const p = e.points[0];
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
}

document.getElementById('refresh').onclick = load;

document.getElementById('close-info').onclick = () => {
  document.getElementById('info').style.display = 'none';
  currentId = null;
};

document.getElementById('delete-btn').onclick = async () => {
  if (!currentId) return;
  if (!confirm('Eliminare definitivamente questo punto?')) return;
  try {
    const r = await fetch('/api/points?id=' + encodeURIComponent(currentId), { method: 'DELETE' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    flash('Punto eliminato');
    document.getElementById('info').style.display = 'none';
    currentId = null;
    await load();
  } catch (e) {
    flash('Eliminazione fallita: ' + e.message, false);
  }
};

load();
</script>
</body>
</html>
"""


class _VizBackend:
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

        vectors: list[list[float]] = []
        meta: list[dict] = []
        for p in all_points:
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
        return {"collection": self.collection, "points": points}

    def delete_point(self, pid: str) -> bool:
        url = f"{self.qdrant_url}/collections/{self.collection}/points/delete"
        # Prova come stringa; se l'id è numerico in Qdrant, tenta cast
        payload_ids: list = [pid]
        try:
            payload_ids = [int(pid)] if pid.isdigit() else [pid]
        except Exception:
            pass
        r = requests.post(url, json={"points": payload_ids}, timeout=15)
        return r.ok


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


class _VizHandler(BaseHTTPRequestHandler):
    server_version = "LTSIA-QdrantViz/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _html(self, code: int, html: str):
        self._send(code, "text/html; charset=utf-8", html.encode("utf-8"))

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._html(200, HTML_PAGE)
            return
        if parsed.path == "/api/points":
            try:
                self._json(200, self.server.backend.fetch_points())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        self._json(404, {"error": "not found"})

    def do_DELETE(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/points":
            qs = parse_qs(parsed.query)
            pid = (qs.get("id") or [""])[0]
            if not pid:
                self._json(400, {"error": "id required"})
                return
            try:
                ok = self.server.backend.delete_point(pid)
                self._json(200 if ok else 500, {"ok": ok})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        self._json(404, {"error": "not found"})


class _VizServer:
    def __init__(self, port: int, backend: _VizBackend):
        self.port = port
        self.backend = backend
        self.httpd = HTTPServer(("127.0.0.1", port), _VizHandler)
        self.httpd.backend = backend
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class QdrantVizTool(BaseTool):
    _server: Optional[_VizServer] = None

    def __init__(self, config=None):
        self.config = config

    def get_name(self) -> str:
        return "qdrant_viz"

    def get_description(self) -> str:
        return (
            "Apre un'interfaccia web 3D dei punti in Qdrant: pallini con label del "
            "content, zoom, rotazione, eliminazione singoli punti. "
            "action: 'start' (avvia, default port 8090), 'stop', 'status'."
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "status"],
                    "description": "start | stop | status (default: start)",
                },
                "port": {
                    "type": "integer",
                    "description": "Porta su cui avviare il server (default 8090)",
                },
            },
        }

    def execute(self, args: dict) -> str:
        action = (args.get("action") or "start").lower()
        if action == "stop":
            return self._stop()
        if action == "status":
            return self._status()
        port = int(args.get("port") or 8090)
        return self._start(port)

    def _start(self, port: int) -> str:
        if QdrantVizTool._server:
            return f"Qdrant Viz già attivo: http://127.0.0.1:{QdrantVizTool._server.port}"
        if not self.config:
            return "ERROR: config non disponibile — tool non può leggere qdrant_url/collection"
        qurl = getattr(self.config, "qdrant_url", None)
        coll = getattr(self.config, "qdrant_collection", None)
        if not qurl or not coll:
            return "ERROR: Qdrant non configurato (manca qdrant_url / qdrant_collection in config)"

        try:
            r = requests.get(f"{qurl}/collections/{coll}", timeout=5)
            if not r.ok:
                return f"ERROR: collection '{coll}' non trovata su {qurl} ({r.status_code})"
        except Exception as e:
            return f"ERROR: Qdrant non raggiungibile su {qurl}: {e}"

        backend = _VizBackend(qurl, coll)
        try:
            srv = _VizServer(port, backend)
            srv.start()
        except OSError as e:
            return f"ERROR: impossibile avviare server su porta {port}: {e}"

        QdrantVizTool._server = srv
        return f"Qdrant Viz avviato su http://127.0.0.1:{port} (collection '{coll}')"

    def _stop(self) -> str:
        srv = QdrantVizTool._server
        if not srv:
            return "Qdrant Viz non è attivo"
        srv.stop()
        QdrantVizTool._server = None
        return "Qdrant Viz fermato"

    def _status(self) -> str:
        srv = QdrantVizTool._server
        if not srv:
            return "Qdrant Viz: non attivo"
        return f"Qdrant Viz: attivo su http://127.0.0.1:{srv.port}"
