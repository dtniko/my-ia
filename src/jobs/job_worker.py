#!/usr/bin/env python3
"""
job_worker.py — Worker standalone per background jobs.

Avviato da JobManager come subprocess indipendente.
Esegue un task ricorrente a intervallo fisso finché non appare .stop o scade run_until.

Invocazione:
  python job_worker.py '<json-config>'

Config JSON: {"job_id": "job_abc123", "job_dir": "/home/user/.ltsia/jobs/job_abc123"}

Output scritti in: $job_dir/outputs/<timestamp>.json
Stop: crea $job_dir/.stop
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import re

# ── Bootstrap ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}")
    job_id  = config.get("job_id", "")
    job_dir = Path(config.get("job_dir", ""))

    if not job_id or not job_dir:
        sys.stderr.write("[job_worker] ERROR: missing job_id or job_dir\n")
        sys.exit(1)

    log_file  = job_dir / "worker.log"
    stop_file = job_dir / ".stop"
    pid_file  = job_dir / ".running"

    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    def worker_log(msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_file, "a") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def load_job_def() -> Optional[dict]:
        f = job_dir / "job.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except Exception:
            return None

    def save_job_def(data: dict) -> None:
        tmp  = job_dir / "job.json.tmp"
        dest = job_dir / "job.json"
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(dest)

    def write_output(job_def: dict, content: str) -> None:
        notif = {
            "job_id":      job_id,
            "description": job_def.get("description", job_def.get("type", "")),
            "type":        job_def.get("type", ""),
            "produced_at": int(time.time()),
            "content":     content,
        }
        ts_str = str(time.time()).replace(".", "_")
        out_file = job_dir / "outputs" / f"{ts_str}.json"
        out_file.write_text(json.dumps(notif, ensure_ascii=False))

    def sleep_interruptible(seconds: int) -> bool:
        """Dorme in chunk da 5s. Ritorna True se ricevuto segnale stop."""
        slept = 0
        while slept < seconds:
            if stop_file.exists():
                return True
            chunk = min(5, seconds - slept)
            time.sleep(chunk)
            slept += chunk
        return False

    worker_log(f"START job_id={job_id}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    while True:
        if stop_file.exists():
            worker_log("STOP signal received — exiting.")
            pid_file.unlink(missing_ok=True)
            sys.exit(0)

        job_def = load_job_def()
        if job_def is None:
            worker_log("ERROR: cannot load job.json — exiting.")
            pid_file.unlink(missing_ok=True)
            sys.exit(1)

        run_until = job_def.get("run_until")
        if run_until is not None and time.time() > int(run_until):
            worker_log(f"Job expired (run_until={run_until}) — exiting.")
            pid_file.unlink(missing_ok=True)
            sys.exit(0)

        task_type        = job_def.get("type", "time_notification")
        params           = job_def.get("params", {})
        interval_seconds = max(10, int(job_def.get("interval_seconds", 60)))

        worker_log(f"Running task type={task_type}")
        try:
            output = execute_task(task_type, params, job_dir)
        except Exception as e:
            output = f"ERROR: {e}"
            worker_log(f"Task exception: {e}")

        if output:
            write_output(job_def, output)
            worker_log(f"Output written ({len(output)} chars)")
        else:
            worker_log("No output (silent result — condition unchanged)")

        job_def["run_count"] = int(job_def.get("run_count", 0)) + 1
        save_job_def(job_def)

        if sleep_interruptible(interval_seconds):
            worker_log("STOP signal received during sleep — exiting.")
            pid_file.unlink(missing_ok=True)
            sys.exit(0)


# ── Task implementations ──────────────────────────────────────────────────────

def execute_task(task_type: str, params: dict, job_dir: Path) -> Optional[str]:
    if task_type == "time_notification":
        return task_time_notification(params)
    elif task_type == "monitor_url":
        return task_monitor_url(params, job_dir)
    elif task_type == "web_search_periodic":
        return task_web_search_periodic(params)
    else:
        return f"ERROR: unknown task type: {task_type}"


def task_time_notification(params: dict) -> str:
    template = params.get("template", "Sono le {time} del {date}")
    now = datetime.now()
    return template.replace("{time}", now.strftime("%H:%M:%S")) \
                   .replace("{date}", now.strftime("%d/%m/%Y")) \
                   .replace("{datetime}", now.strftime("%d/%m/%Y %H:%M:%S"))


def task_monitor_url(params: dict, job_dir: Path) -> Optional[str]:
    url = params.get("url", "")
    if not url:
        return "ERROR: monitor_url requires url parameter"

    content = worker_fetch_url(url, 15)
    if content is None:
        return f"ERRORE: impossibile raggiungere {url}"

    # Estrai solo testo per il confronto
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text).strip()
    new_hash = hashlib.md5(text.encode()).hexdigest()

    state_file = job_dir / "monitor_state.json"
    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass

    prev_hash = state.get("hash", "")
    state["hash"]       = new_hash
    state["checked_at"] = int(time.time())
    state["url"]        = url
    state_file.write_text(json.dumps(state, ensure_ascii=False))

    if not prev_hash:
        return f"Prima verifica di {url} completata. Monitoraggio attivo."

    if new_hash == prev_hash:
        return None  # Nessun cambiamento — silenzioso

    preview = text[:300]
    return f"CAMBIAMENTO RILEVATO su {url}\n\nAnteprima del nuovo contenuto:\n{preview}"


def task_web_search_periodic(params: dict) -> Optional[str]:
    query      = params.get("query", "")
    max_results = int(params.get("max_results", 5))

    if not query:
        return "ERROR: web_search_periodic requires query parameter"

    results = worker_search_ddg(query, max_results)
    if not results:
        return f"Nessun risultato trovato per: {query}"

    lines = [f"Risultati di ricerca per: {query}\n"]
    for i, r in enumerate(results, 1):
        title   = r.get("title", "?")
        url     = r.get("url", "")
        snippet = r.get("snippet", "")
        line = f"{i}. {title}"
        if url:     line += f"\n   {url}"
        if snippet: line += f"\n   {snippet}"
        lines.append(line)

    return "\n".join(lines)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def worker_fetch_url(url: str, timeout: int = 10) -> Optional[str]:
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LTSIA-Worker/1.0)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(charset, errors="replace")
    except Exception:
        return None


def worker_search_ddg(query: str, max_results: int) -> list[dict]:
    import urllib.parse
    url  = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query) + "&kl=it-it"
    html = worker_fetch_url(url, 15)
    if not html:
        return []

    results = []

    # Estrai blocchi risultato
    for block in re.findall(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL | re.IGNORECASE):
        if len(results) >= max_results:
            break
        m = re.search(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        href  = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()

        # Risolvi redirect DDG
        if "duckduckgo.com/l/?" in href:
            import urllib.parse as up
            qs = up.parse_qs(up.urlparse(href).query)
            href = qs.get("uddg", [href])[0]

        snippet = ""
        ms = re.search(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE)
        if ms:
            snippet = re.sub(r"<[^>]+>", "", ms.group(1)).strip()

        results.append({"title": title, "url": href, "snippet": snippet})

    # Fallback lite DDG
    if not results:
        import urllib.parse
        lite_url  = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query)
        lite_html = worker_fetch_url(lite_url, 15)
        if lite_html:
            for href, title in re.findall(r'<a[^>]+href="(https?[^"]+)"[^>]*>([^<]+)</a>', lite_html, re.IGNORECASE):
                if len(results) >= max_results:
                    break
                results.append({"title": title.strip(), "url": href, "snippet": ""})

    return results
