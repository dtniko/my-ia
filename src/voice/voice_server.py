"""
VoiceServer — WebSocket server asyncio per la modalità voce.

Protocollo JSON su WebSocket:
  Client → Server:
    {"type":"message",     "text":"..."}              testo diretto (fallback)
    {"type":"audio_chunk", "data":"<b64 f32>",        chunk PCM float32 mono
                           "sr":16000}
    {"type":"confirm_yes"}                            conferma "sono io"
    {"type":"confirm_no"}                             nega "non ero io"
    {"type":"enroll_request"}                         avvia enrollment da UI
    {"type":"snapshot_request"}                       richiede snapshot dashboard

  Server → Client:
    {"type":"snapshot",      "payload":{...}}         snapshot completo dashboard
    {"type":"jobs_update",   "jobs":[...],            push periodico stato job
                             "job_logs":[...]}
    {"type":"chunk",         "text":"..."}            chunk streaming risposta
    {"type":"done",          "full":"..."}            risposta completa
    {"type":"tool",          "name":"..."}            tool in esecuzione
    {"type":"status",        "state":"idle|thinking"} cambio stato agente
    {"type":"audio",         "data":"<b64>",          audio edge-tts risposta
                             "format":"mp3"}
    {"type":"notification",  "description":"...",     notifica job background
                             "content":"...",
                             "data":"<b64>|''",
                             "format":"mp3"}
    {"type":"stats",         "tokens":N,"max":N,      statistiche context
                             "pct":N,"compacting":F}
    {"type":"error",         "message":"..."}         errore
    {"type":"speaker_status","state":"paused|resumed"} stato ascolto
    {"type":"confirm_speaking","transcript":"..."}    chiede conferma speaker
    {"type":"enroll_needed"}                          nessun voice print presente
    {"type":"enroll_start",  "duration":N,            enrollment avviato
                             "message":"..."}
    {"type":"enroll_progress","pct":N}                avanzamento enrollment
    {"type":"enroll_done",   "message":"..."}         enrollment completato
    {"type":"enroll_error",  "message":"..."}         errore enrollment
    {"type":"stt_text",      "text":"..."}            testo trascritto (debug)
"""
from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

# File di timing: ~/.ltsia/logs/timing.log
_TIMING_LOG = os.path.expanduser("~/.ltsia/logs/timing.log")
os.makedirs(os.path.dirname(_TIMING_LOG), exist_ok=True)
_timing_logger = logging.getLogger("ltsia.timing")
_timing_logger.setLevel(logging.DEBUG)
if not _timing_logger.handlers:
    _fh = logging.FileHandler(_TIMING_LOG, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    _timing_logger.addHandler(_fh)

def _tlog(msg: str) -> None:
    print(msg, flush=True)
    _timing_logger.debug(msg)

import numpy as np

if TYPE_CHECKING:
    from src.agents.chat_agent  import ChatAgent
    from src.config             import Config
    from src.jobs.job_manager   import JobManager


def _detect_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip != "0.0.0.0" else ""
    except Exception:
        return ""


class VoiceServer:
    def __init__(
        self,
        chat_agent        : "ChatAgent",
        config            : "Config",
        job_manager       : Optional["JobManager"] = None,
        snapshot_provider : Optional[Callable[[], dict]] = None,
        qdrant_viz_backend = None,
    ):
        self.chat_agent          = chat_agent
        self.config              = config
        self.job_manager         = job_manager
        self.snapshot_provider   = snapshot_provider
        self._qdrant_viz_backend = qdrant_viz_backend

        # Stato connessione
        self._is_busy   = False
        self._active_ws = None
        self._pipeline  = None
        self._notif_queue: Optional[asyncio.Queue] = None

        # Contatori per rate logging messaggi WS in entrata
        self._ws_msg_count  : int   = 0
        self._ws_audio_count: int   = 0
        self._ws_rate_t0    : float = 0.0

        # Componenti audio (inizializzati in _serve)
        self._verifier = None
        self._stt      = None

        # Signal di pronto: il main thread può aspettare su questo event
        # prima di stampare il prompt del REPL.
        self.ready: threading.Event = threading.Event()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self, port: int = 8765, http_port: int = 8080) -> None:
        """Avvia il server (bloccante). Pensato per girare in un thread daemon."""
        try:
            import websockets  # noqa: F401
        except ImportError:
            print("[voice] websockets non installato — esegui: pip install websockets")
            return

        from src.voice.tts import resolve_tts_voice

        self._tts_voice = resolve_tts_voice(self.config.tts_voice)
        self._tts_rate  = getattr(self.config, "tts_rate", "+0%")
        self._port      = port

        self._start_http_server(http_port, ws_port=port)

        # Precarica i modelli pesanti in sequenza nel thread corrente,
        # PRIMA che asyncio e ThreadPoolExecutor inizino. Questo evita che
        # PyTorch e ctranslate2 inizializzino OpenMP concorrentemente nei
        # thread worker → segfault.
        self._init_audio_components()
        self._preload_models()

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve(port))
        except Exception as e:
            print(f"[voice] Errore server: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_default_executor())
            except Exception:
                pass
            self._loop.close()
            self.ready.set()

    def _start_http_server(self, http_port: int, ws_port: int = 8765) -> None:
        """Avvia un HTTP server per servire voice/dist/ e la rotta /viz."""
        self._http_port = None
        dist_dir = Path(__file__).parent.parent.parent / "voice" / "dist"
        if not dist_dir.is_dir():
            self._http_error = f"dist non trovata ({dist_dir}) — esegui: cd voice && npm run build"
            return

        from src.tools.qdrant_viz.viz_tool import _ws_html

        class _Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?")[0] == "/viz":
                    body = _ws_html(ws_port).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

            def log_message(self, fmt, *args):
                pass

        handler = functools.partial(_Handler, directory=str(dist_dir))

        try:
            httpd = http.server.HTTPServer(("0.0.0.0", http_port), handler)
        except OSError as exc:
            self._http_error = f"porta {http_port} non disponibile: {exc}"
            return

        t = threading.Thread(target=httpd.serve_forever, daemon=True, name="voice-http")
        t.start()
        self._http_port = http_port
        self._http_error = None

    def print_startup_banner(self) -> None:
        """Stampa il riepilogo di avvio. Va chiamato dal main thread dopo ready.wait()."""
        local_ip  = getattr(self, "_startup_local_ip", "")
        ws_port   = getattr(self, "_startup_ws_port",  self._port)
        http_port = getattr(self, "_http_port",  None)
        http_err  = getattr(self, "_http_error", None)

        if self._verifier is not None:
            enrolled = self._verifier.is_enrolled
            sr_label = "attivo" if enrolled else "nessun voice print — «Daniela allena il riconoscimento vocale»"
        else:
            sr_label = "non disponibile"

        tts_label = self._tts_voice if self._tts_voice else "non disponibile (usa TTS browser)"

        print(f"[voice] ┌─ Voice server pronto ─────────────────────────────")
        if http_port:
            print(f"[voice] │  Frontend   http://localhost:{http_port}")
            if local_ip:
                print(f"[voice] │             http://{local_ip}:{http_port}  ← rete locale")
        else:
            print(f"[voice] │  Frontend   non disponibile — {http_err}")
        print(f"[voice] │  WebSocket  ws://localhost:{ws_port}")
        if local_ip:
            print(f"[voice] │             ws://{local_ip}:{ws_port}  ← rete locale")
        print(f"[voice] │  TTS        {tts_label}")
        print(f"[voice] │  Speaker    {sr_label}")
        print(f"[voice] └───────────────────────────────────────────────────")

    def stop(self) -> None:
        """Ferma il server asyncio dal thread principale."""
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            return
        stop_event = getattr(self, "_stop_event_async", None)
        if stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)
        elif loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    def _preload_models(self) -> None:
        """
        Carica i modelli ML in sequenza nel thread corrente.
        Ordine: STT (ctranslate2) prima, poi ECAPA (PyTorch).
        Entrambi usano OpenMP: inizializzarli in sequenza evita conflitti
        tra i due runtime quando vengono caricati da thread worker concorrenti.
        """
        if self._stt is not None:
            try:
                self._stt._load()
            except Exception as exc:
                print(f"[voice] Preload STT fallito: {exc}")

        if self._verifier is not None:
            try:
                self._verifier._load_model()
            except Exception as exc:
                print(f"[voice] Preload verifier fallito: {exc}")

    # ── Server loop ───────────────────────────────────────────────────────────

    async def _serve(self, port: int) -> None:
        import websockets

        self._notif_queue = asyncio.Queue()
        # Salva le info di startup per print_startup_banner() chiamato dal main thread
        self._startup_local_ip = _detect_local_ip()
        self._startup_ws_port  = port

        bg_tasks = []
        if self.job_manager:
            bg_tasks.append(asyncio.create_task(self._poll_job_notifications()))
            bg_tasks.append(asyncio.create_task(self._drain_notifications()))
            bg_tasks.append(asyncio.create_task(self._broadcast_jobs()))

        self._stop_event_async = asyncio.Event()
        async with websockets.serve(self._handle_client, "0.0.0.0", port):
            # Segnala al main thread che il voice server è pronto: STT caricato,
            # verifier pronto, websocket in ascolto. Da qui il REPL può stampare
            # il prompt senza rischio di sovrapposizioni.
            self.ready.set()
            await self._stop_event_async.wait()

        for t in bg_tasks:
            t.cancel()
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)

    def _init_audio_components(self) -> None:
        """Istanzia i componenti audio. I modelli vengono caricati lazily."""
        try:
            from src.voice.stt_engine import STTEngine
            model_size  = getattr(self.config, "stt_model", "tiny")
            cpu_threads = int(getattr(self.config, "stt_cpu_threads", 4))
            self._stt   = STTEngine(model_size=model_size, language="it", cpu_threads=cpu_threads)

            if getattr(self.config, "speaker_verify", True):
                from src.voice.speaker_verifier import SpeakerVerifier
                self._verifier = SpeakerVerifier()
            else:
                print("[voice] Speaker verification disabilitato (speaker_verify=false)")
                self._verifier = None
        except Exception as exc:
            print(f"[voice] Audio pipeline non disponibile: {exc}")
            self._verifier = None
            self._stt      = None

    # ── Client handler ────────────────────────────────────────────────────────

    async def _handle_client(self, websocket) -> None:
        loop = asyncio.get_event_loop()
        from src.voice.tts import generate_tts_audio

        self._active_ws      = websocket
        self._pipeline       = None
        self._ws_msg_count   = 0
        self._ws_audio_count = 0
        self._ws_rate_t0     = time.monotonic()

        try:
            # Costruisci pipeline audio se disponibile
            if self._verifier is not None and self._stt is not None:
                self._pipeline = self._build_pipeline(websocket, loop)
            pipeline = self._pipeline

            # Stato iniziale
            await self._send(websocket, {"type": "status", "state": "idle"})
            await self._send_stats(websocket)

            # Informa se manca il voice print
            if pipeline is not None and not self._verifier.is_enrolled:
                await self._send(websocket, {"type": "enroll_needed"})

            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue

                msg_type = msg.get("type", "message")

                # ── Rate logging messaggi WS ───────────────────────────────
                self._ws_msg_count += 1
                if msg_type == "audio_chunk":
                    self._ws_audio_count += 1
                now_t = time.monotonic()
                elapsed_t = now_t - self._ws_rate_t0
                if elapsed_t >= 10.0:
                    msg_rate   = self._ws_msg_count   / elapsed_t
                    audio_rate = self._ws_audio_count / elapsed_t
                    print(f"[ws] rate: {msg_rate:.1f} msg/s | {audio_rate:.1f} audio_chunk/s "
                          f"({self._ws_msg_count} msg in {elapsed_t:.0f}s)")
                    self._ws_msg_count   = 0
                    self._ws_audio_count = 0
                    self._ws_rate_t0     = now_t

                # ── Chunk audio dal browser ────────────────────────────────
                if msg_type == "audio_chunk":
                    if pipeline is None:
                        continue
                    data_b64 = msg.get("data", "")
                    sr       = int(msg.get("sr", 16000))
                    if not data_b64:
                        continue
                    try:
                        raw_bytes = base64.b64decode(data_b64)
                        audio     = np.frombuffer(raw_bytes, dtype=np.float32).copy()
                        pipeline.push_audio_chunk(audio, sr)
                    except Exception as exc:
                        print(f"[voice] Errore decodifica audio: {exc}")
                    continue

                # ── Risposte conferma speaker ──────────────────────────────
                if msg_type == "confirm_yes":
                    pipeline and pipeline.handle_confirm_response(True)
                    continue
                if msg_type == "confirm_no":
                    pipeline and pipeline.handle_confirm_response(False)
                    continue

                # ── Enrollment da UI ───────────────────────────────────────
                if msg_type == "enroll_request":
                    if pipeline is not None:
                        await loop.run_in_executor(None, pipeline.start_enrollment)
                    continue

                # ── Dashboard snapshot on-demand ───────────────────────────
                if msg_type == "snapshot_request":
                    await self._send_snapshot(websocket, loop)
                    continue

                # ── Qdrant Viz ─────────────────────────────────────────────
                if msg_type == "qdrant_fetch":
                    await self._handle_qdrant_fetch(websocket, loop)
                    continue
                if msg_type == "qdrant_delete":
                    await self._handle_qdrant_delete(websocket, loop, msg.get("id", ""))
                    continue

                # ── Messaggio testo tradizionale (fallback/debug) ──────────
                if msg_type == "message":
                    text = msg.get("text", "").strip()
                    if text:
                        await self._run_chat(websocket, text, loop)
                    continue

        except Exception:
            pass  # client disconnesso
        finally:
            if pipeline:
                pipeline.shutdown()
            if self._active_ws is websocket:
                self._active_ws = None
            self._pipeline  = None
            self._is_busy   = False

    # ── Qdrant Viz ────────────────────────────────────────────────────────────

    async def _handle_qdrant_fetch(self, websocket, loop: asyncio.AbstractEventLoop) -> None:
        backend = self._qdrant_viz_backend
        if backend is None:
            await self._send(websocket, {"type": "qdrant_points", "collection": "", "points": []})
            return
        try:
            result = await loop.run_in_executor(None, backend.fetch_points)
            await self._send(websocket, {"type": "qdrant_points", **result})
        except Exception as exc:
            await self._send(websocket, {"type": "error", "message": f"qdrant_fetch: {exc}"})

    async def _handle_qdrant_delete(self, websocket, loop: asyncio.AbstractEventLoop, pid: str) -> None:
        backend = self._qdrant_viz_backend
        if backend is None or not pid:
            await self._send(websocket, {"type": "qdrant_delete_result", "ok": False})
            return
        try:
            ok = await loop.run_in_executor(None, lambda: backend.delete_point(pid))
            await self._send(websocket, {"type": "qdrant_delete_result", "ok": bool(ok)})
        except Exception as exc:
            await self._send(websocket, {"type": "error", "message": f"qdrant_delete: {exc}"})

    def _build_pipeline(self, websocket, loop: asyncio.AbstractEventLoop):
        """Crea una pipeline per la connessione corrente."""
        from src.voice.vad_processor import VADProcessor
        from src.voice.audio_pipeline import AudioPipeline

        vad = VADProcessor(aggressiveness=3)

        async def send_fn(msg: dict) -> None:
            await self._send(websocket, msg)

        async def chat_fn(text: str) -> None:
            await self._run_chat(websocket, text, loop)

        return AudioPipeline(
            verifier = self._verifier,
            vad      = vad,
            stt      = self._stt,
            loop     = loop,
            send_fn  = send_fn,
            chat_fn  = chat_fn,
        )

    # ── Chat runner ───────────────────────────────────────────────────────────

    async def _run_chat(self, websocket, text: str, loop: asyncio.AbstractEventLoop) -> None:
        """
        Esegue una richiesta al ChatAgent e invia la risposta al client.

        Streaming TTS: mentre il LLM produce chunk, il testo è bufferato e
        spezzato in frasi. Appena una frase è completa viene sintetizzata e
        inviata al client — così la prima frase parte molto prima rispetto
        ad aspettare la risposta intera.
        """
        from src.voice.tts import generate_tts_audio

        if self._is_busy:
            return

        # ── Timing end-to-end ────────────────────────────────────────────────
        _t0      = time.monotonic()
        _pipeline = self._pipeline
        _t_vad   = _pipeline._t_vad_end if _pipeline is not None else _t0

        def _dt(label: str) -> None:
            from_vad  = (time.monotonic() - _t_vad) * 1000
            from_chat = (time.monotonic() - _t0)    * 1000
            _tlog(f"[timing] +{from_vad:6.0f}ms (chat+{from_chat:5.0f}ms)  {label}")

        _dt("_run_chat avviato (loop asyncio)")

        self._is_busy = True
        # Silenzia la pipeline: l'IA sta per parlare, non vogliamo che si ascolti
        if self._pipeline:
            self._pipeline.set_deaf(True)
        await self._send(websocket, {"type": "status", "state": "thinking"})

        # ── TTS worker: sintetizza frasi in ordine e le invia al client ──────
        sentence_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        _first_tts_logged = False

        async def tts_worker() -> None:
            nonlocal _first_tts_logged
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:  # sentinel di chiusura
                    return
                t_tts = time.monotonic()
                audio_b64 = await loop.run_in_executor(
                    None,
                    lambda s=sentence: generate_tts_audio(
                        s, self._tts_voice, self._tts_rate
                    ),
                )
                tts_ms = (time.monotonic() - t_tts) * 1000
                if audio_b64:
                    if not _first_tts_logged:
                        _first_tts_logged = True
                        _dt(f"primo audio TTS pronto e inviato (sintesi {tts_ms:.0f}ms)")
                    await self._send(websocket, {
                        "type":   "audio",
                        "data":   audio_b64,
                        "format": "mp3",
                    })

        tts_task = asyncio.create_task(tts_worker()) if self._tts_voice else None

        # Boundary frase: punto/esclamativo/domanda seguiti da spazio o fine riga.
        # Non spezza abbreviazioni come "e.g." o numeri decimali.
        _sent_re = re.compile(r"(?<=[.!?])[ \t]+|\n+")

        # Stato filtro tool call per il TTS (stateful su più chunk)
        _tts_hold     = ""   # buffer look-ahead per rilevare tag a cavallo di chunk
        _tts_in_call  = False
        _TOPEN        = "<tool_call>"
        _TCLOSE       = "</tool_call>"
        _HOLD_LEN     = max(len(_TOPEN), len(_TCLOSE)) - 1

        sentence_buf = ""

        def _tts_filter(chunk: str) -> str:
            """Rimuove blocchi <tool_call>…</tool_call> dal testo destinato al TTS."""
            nonlocal _tts_hold, _tts_in_call
            _tts_hold += chunk
            out = ""
            while _tts_hold:
                if not _tts_in_call:
                    idx = _tts_hold.find(_TOPEN)
                    if idx == -1:
                        # Nessun tag aperto: emetti tutto tranne gli ultimi N char
                        # (potrebbero essere l'inizio di un tag)
                        safe = len(_tts_hold) - _HOLD_LEN
                        if safe > 0:
                            out += _tts_hold[:safe]
                            _tts_hold = _tts_hold[safe:]
                        break
                    out += _tts_hold[:idx]
                    _tts_hold = _tts_hold[idx + len(_TOPEN):]
                    _tts_in_call = True
                else:
                    idx = _tts_hold.find(_TCLOSE)
                    if idx == -1:
                        # Dentro un tool call: scarta tutto tranne la coda
                        safe = len(_tts_hold) - _HOLD_LEN
                        if safe > 0:
                            _tts_hold = _tts_hold[safe:]
                        break
                    _tts_hold = _tts_hold[idx + len(_TCLOSE):]
                    _tts_in_call = False
            return out

        def _tts_flush_hold() -> str:
            """Emette i char rimasti nel buffer look-ahead a fine streaming."""
            nonlocal _tts_hold, _tts_in_call
            out = "" if _tts_in_call else _tts_hold
            _tts_hold = ""
            _tts_in_call = False
            return out

        def _clean_for_tts(text: str) -> str:
            """Rimuove formattazione markdown e simboli speciali prima del TTS."""
            # Cattura i gruppi (testo nei link, bold, italic) e li mantiene
            text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
            text = re.sub(r"`([^`]*)`", r"\1", text)
            text = re.sub(r"\*{1,3}([^*\n]*?)\*{1,3}", r"\1", text)
            text = re.sub(r"_{1,3}([^_\n]*?)_{1,3}", r"\1", text)
            text = re.sub(r"~{2}([^~]*?)~{2}", r"\1", text)
            text = re.sub(r"#{1,6}\s*", "", text)
            text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
            text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
            text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
            text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
            text = re.sub(r"[|]{1}", " ", text)
            text = re.sub(r"^[-=]{2,}\s*$", "", text, flags=re.MULTILINE)
            # Simboli residui che il TTS legge letteralmente
            text = re.sub(r"[*_~`#\\]", "", text)
            # Spazi multipli/newline in eccesso
            text = re.sub(r"\n{2,}", " ", text)
            text = re.sub(r"\s{2,}", " ", text)
            return text.strip()

        def _enqueue_sentence(sentence: str) -> None:
            sentence = _clean_for_tts(sentence.strip())
            if not sentence or tts_task is None:
                return
            asyncio.run_coroutine_threadsafe(sentence_queue.put(sentence), loop)

        _first_chunk_logged = False

        def on_chunk(chunk: str) -> None:
            nonlocal sentence_buf, _first_chunk_logged
            if not _first_chunk_logged:
                _first_chunk_logged = True
                _dt("primo chunk LLM ricevuto")
            # Invia il chunk grezzo al client per aggiornare la UI
            asyncio.run_coroutine_threadsafe(
                self._send(websocket, {"type": "chunk", "text": chunk}),
                loop,
            )
            # Filtra i blocchi tool call prima di passare al TTS
            tts_text = _tts_filter(chunk)
            sentence_buf += tts_text
            # Estrae frasi complete e le manda al worker TTS
            while True:
                m = _sent_re.search(sentence_buf)
                if not m:
                    break
                end = m.end()
                sentence, sentence_buf = sentence_buf[:end], sentence_buf[end:]
                _enqueue_sentence(sentence)

        _TOOL_ANNOUNCE = {
            "web_search":             lambda a: f"Cerco {a.get('query', '')}.",
            "web_fetch":              lambda a: "Leggo la pagina.",
            "execute_command":        lambda a: f"Eseguo: {str(a.get('command',''))[:60]}.",
            "write_file":             lambda a: f"Scrivo il file {a.get('path', a.get('filename',''))}.",
            "read_file":              lambda a: f"Leggo il file {a.get('path', a.get('filename',''))}.",
            "create_directory":       lambda a: f"Creo la cartella {a.get('path','')}.",
            "list_directory":         lambda a: "Elenco i file.",
            "glob_search":            lambda a: f"Cerco file con pattern {a.get('pattern','')}.",
            "grep_search":            lambda a: f"Cerco nel codice: {a.get('pattern','')}.",
            "plan_project":           lambda a: "Pianificando il progetto.",
            "delegate_file_creation": lambda a: "Genero i file.",
            "run_tests":              lambda a: "Eseguo i test.",
            "smart_install":          lambda a: f"Installo {a.get('packages', a.get('package',''))}.",
            "create_module":          lambda a: f"Creo il modulo {a.get('name','')}.",
            "remember":               lambda a: "Salvo in memoria.",
            "search_memory":          lambda a: f"Cerco in memoria: {a.get('query','')}.",
            "screenshot":             lambda a: "Scatto uno screenshot.",
            "applescript":            lambda a: "Eseguo uno script.",
            "schedule_job":           lambda a: "Programmo un job.",
        }

        def _tool_announcement(tool_name: str, args: dict) -> str:
            fn = _TOOL_ANNOUNCE.get(tool_name)
            if fn:
                try:
                    return fn(args)
                except Exception:
                    pass
            return f"Uso {tool_name.replace('_', ' ')}."

        def on_tool_start(tool_name: str, args: dict) -> None:
            _dt(f"tool '{tool_name}' avviato")
            asyncio.run_coroutine_threadsafe(
                self._send(websocket, {"type": "tool", "name": tool_name}),
                loop,
            )
            if self._tts_voice:
                announcement = _tool_announcement(tool_name, args)
                asyncio.run_coroutine_threadsafe(
                    sentence_queue.put(announcement),
                    loop,
                )

        _dt("run_in_executor avviato → chat_agent.chat()")
        try:
            response: str = await loop.run_in_executor(
                None,
                lambda t=text: self.chat_agent.chat(t, on_stream=on_chunk, on_tool_start=on_tool_start),
            )
        except Exception as exc:
            if tts_task is not None:
                await sentence_queue.put(None)
                await tts_task
            await self._send(websocket, {"type": "error", "message": str(exc)})
            await self._send(websocket, {"type": "status", "state": "idle"})
            self._is_busy = False
            if self._pipeline:
                self._pipeline.set_deaf(False)
            return

        # Flush residuo: l'ultima frase potrebbe non avere terminatore finale.
        # Va messo in coda con await diretto PRIMA del sentinel None, altrimenti
        # run_coroutine_threadsafe la schedula dopo che il worker ha già ricevuto
        # None e terminato.
        # Svuota anche il buffer look-ahead del filtro tag (ultimi _HOLD_LEN char
        # trattenuti per prevenire false aperture di tag a cavallo di chunk).
        residual = (sentence_buf + _tts_flush_hold()).strip()
        sentence_buf = ""

        if tts_task is not None:
            if residual:
                await sentence_queue.put(residual)
            await sentence_queue.put(None)   # sentinel di chiusura
            await tts_task

        _dt("LLM completato, risposta intera pronta")
        await self._send(websocket, {"type": "done", "full": response})
        await self._send_stats(websocket)
        self._is_busy = False
        # Riattiva ascolto dopo che la risposta (incluso TTS) è stata inviata
        if self._pipeline:
            self._pipeline.set_deaf(False)

    # ── Notifiche job background ──────────────────────────────────────────────

    async def _poll_job_notifications(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(5)
            try:
                outputs = await loop.run_in_executor(
                    None, self.job_manager.collect_pending_outputs
                )
                for out in outputs:
                    await self._notif_queue.put(out)
            except Exception:
                pass

    async def _drain_notifications(self) -> None:
        loop = asyncio.get_event_loop()
        from src.voice.tts import generate_tts_audio

        while True:
            await asyncio.sleep(1)
            if self._is_busy or self._active_ws is None:
                continue
            if self._notif_queue.empty():
                continue

            while not self._notif_queue.empty():
                out     = self._notif_queue.get_nowait()
                desc    = out.get("description", out.get("type", "job"))
                content = out.get("content", "")
                tts_txt = f"{desc}. {content}" if content else desc

                audio_b64 = ""
                if self._tts_voice:
                    audio_b64 = await loop.run_in_executor(
                        None,
                        lambda t=tts_txt: generate_tts_audio(t, self._tts_voice, self._tts_rate),
                    )

                await self._send(self._active_ws, {
                    "type":        "notification",
                    "description": desc,
                    "content":     content,
                    "data":        audio_b64,
                    "format":      "mp3",
                })
                self._notif_queue.task_done()

    # ── Dashboard ─────────────────────────────────────────────────────────────

    async def _send_snapshot(self, websocket, loop: asyncio.AbstractEventLoop) -> None:
        if not self.snapshot_provider:
            return
        try:
            payload = await loop.run_in_executor(None, self.snapshot_provider)
        except Exception as exc:
            await self._send(websocket, {"type": "error", "message": f"snapshot: {exc}"})
            return
        await self._send(websocket, {"type": "snapshot", "payload": payload})

    async def _broadcast_jobs(self) -> None:
        """Push periodico di job + job_logs ai client. Frequenza: 5s."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(5)
            if self._active_ws is None or self.job_manager is None:
                continue
            try:
                jobs = await loop.run_in_executor(
                    None, lambda: [j.to_dict() for j in self.job_manager.list_jobs()]
                )
                logs = await loop.run_in_executor(
                    None, lambda: self.job_manager.get_output_history(limit=30)
                )
            except Exception:
                continue
            await self._send(self._active_ws, {
                "type":     "jobs_update",
                "jobs":     jobs,
                "job_logs": logs,
            })

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _send(self, websocket, data: dict) -> None:
        try:
            await websocket.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    async def _send_stats(self, websocket) -> None:
        try:
            st  = self.chat_agent.get_stats()
            cw  = self.config.context_window
            tok = st.get("estimated_tokens", 0)
            pct = int(tok / cw * 100) if cw > 0 else 0
            await self._send(websocket, {
                "type":       "stats",
                "tokens":     tok,
                "max":        cw,
                "pct":        pct,
                "compacting": False,
            })
        except Exception:
            pass
