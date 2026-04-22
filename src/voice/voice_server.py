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
import json
import re
import socket
import threading
from typing import TYPE_CHECKING, Callable, Optional

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
    ):
        self.chat_agent        = chat_agent
        self.config            = config
        self.job_manager       = job_manager
        self.snapshot_provider = snapshot_provider

        # Stato connessione
        self._is_busy   = False
        self._active_ws = None
        self._pipeline  = None
        self._notif_queue: Optional[asyncio.Queue] = None

        # Componenti audio (inizializzati in _serve)
        self._verifier = None
        self._stt      = None

        # Signal di pronto: il main thread può aspettare su questo event
        # prima di stampare il prompt del REPL.
        self.ready: threading.Event = threading.Event()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self, port: int = 8765) -> None:
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

        # Precarica i modelli pesanti in sequenza nel thread corrente,
        # PRIMA che asyncio e ThreadPoolExecutor inizino. Questo evita che
        # PyTorch e ctranslate2 inizializzino OpenMP concorrentemente nei
        # thread worker → segfault.
        self._init_audio_components()
        self._preload_models()

        try:
            asyncio.run(self._serve(port))
        except Exception as e:
            print(f"[voice] Errore server: {e}")
        finally:
            # Se qualcosa è andato storto prima del set, sblocchiamo comunque il main.
            self.ready.set()

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
        local_ip = _detect_local_ip()
        print(f"[voice] WebSocket in ascolto su ws://0.0.0.0:{port}")
        print(f"[voice]   localhost:   ws://localhost:{port}")
        if local_ip:
            print(f"[voice]   rete locale: ws://{local_ip}:{port}  ← usa questo dal cellulare")
        if self._tts_voice:
            print(f"[voice]   edge-tts attivo — voce: {self._tts_voice}")
        else:
            print("[voice]   edge-tts non trovato — il client userà TTS browser")

        if self._verifier is not None:
            enrolled = self._verifier.is_enrolled
            print(f"[voice]   speaker recognition: {'attivo' if enrolled else 'nessun voice print — usa «Daniela allena il riconoscimento vocale»'}")
        else:
            print("[voice]   speaker recognition: non disponibile (installa resemblyzer, webrtcvad, faster-whisper)")

        if self.job_manager:
            asyncio.create_task(self._poll_job_notifications())
            asyncio.create_task(self._drain_notifications())
            asyncio.create_task(self._broadcast_jobs())

        async with websockets.serve(self._handle_client, "0.0.0.0", port):
            # Segnala al main thread che il voice server è pronto: STT caricato,
            # verifier pronto, websocket in ascolto. Da qui il REPL può stampare
            # il prompt senza rischio di sovrapposizioni.
            self.ready.set()
            await asyncio.Future()

    def _init_audio_components(self) -> None:
        """Istanzia i componenti audio. I modelli vengono caricati lazily."""
        try:
            from src.voice.speaker_verifier import SpeakerVerifier
            from src.voice.stt_engine       import STTEngine
            self._verifier = SpeakerVerifier()
            model_size     = getattr(self.config, "stt_model", "small")
            self._stt      = STTEngine(model_size=model_size, language="it")
        except Exception as exc:
            print(f"[voice] Audio pipeline non disponibile: {exc}")
            self._verifier = None
            self._stt      = None

    # ── Client handler ────────────────────────────────────────────────────────

    async def _handle_client(self, websocket) -> None:
        loop = asyncio.get_event_loop()
        from src.voice.tts import generate_tts_audio

        self._active_ws = websocket
        self._pipeline  = None

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

    def _build_pipeline(self, websocket, loop: asyncio.AbstractEventLoop):
        """Crea una pipeline per la connessione corrente."""
        from src.voice.vad_processor import VADProcessor
        from src.voice.audio_pipeline import AudioPipeline

        vad = VADProcessor(aggressiveness=2)

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

        self._is_busy = True
        # Silenzia la pipeline: l'IA sta per parlare, non vogliamo che si ascolti
        if self._pipeline:
            self._pipeline.set_deaf(True)
        await self._send(websocket, {"type": "status", "state": "thinking"})

        # ── TTS worker: sintetizza frasi in ordine e le invia al client ──────
        sentence_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        async def tts_worker() -> None:
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:  # sentinel di chiusura
                    return
                audio_b64 = await loop.run_in_executor(
                    None,
                    lambda s=sentence: generate_tts_audio(
                        s, self._tts_voice, self._tts_rate
                    ),
                )
                if audio_b64:
                    await self._send(websocket, {
                        "type":   "audio",
                        "data":   audio_b64,
                        "format": "mp3",
                    })

        tts_task = asyncio.create_task(tts_worker()) if self._tts_voice else None

        # Boundary frase: punto/esclamativo/domanda seguiti da spazio,
        # oppure newline. Evita di spezzare "e.g." o numeri decimali.
        _sent_re = re.compile(r"(?<=[.!?])\s+|\n+")
        sentence_buf = ""

        def _flush(sentence: str) -> None:
            sentence = sentence.strip()
            if not sentence or tts_task is None:
                return
            asyncio.run_coroutine_threadsafe(
                sentence_queue.put(sentence), loop
            )

        def on_chunk(chunk: str) -> None:
            nonlocal sentence_buf
            # Inoltra il chunk testuale subito (il client aggiorna la UI)
            asyncio.run_coroutine_threadsafe(
                self._send(websocket, {"type": "chunk", "text": chunk}),
                loop,
            )
            sentence_buf += chunk
            # Estrae tutte le frasi complete disponibili
            while True:
                m = _sent_re.search(sentence_buf)
                if not m:
                    break
                end = m.end()
                sentence, sentence_buf = sentence_buf[:end], sentence_buf[end:]
                _flush(sentence)

        try:
            response: str = await loop.run_in_executor(
                None,
                lambda t=text: self.chat_agent.chat(t, on_stream=on_chunk),
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

        # Flush della coda residua (ultima frase senza terminatore)
        if sentence_buf.strip():
            _flush(sentence_buf)
            sentence_buf = ""

        # Attende che il worker abbia finito di inviare tutto l'audio
        if tts_task is not None:
            await sentence_queue.put(None)
            await tts_task

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
