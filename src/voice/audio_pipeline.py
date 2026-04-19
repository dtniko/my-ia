"""
AudioPipeline — pipeline completa: audio grezzo → VAD → speaker verify → STT → testo.

Flusso:
  1. Il WebSocket server chiama push_audio_chunk() ad ogni chunk ricevuto dal browser.
  2. Un VAD streaming (frame-per-frame) accumula i frame e rileva segmenti di parlato.
  3. Quando un segmento termina (silenzio ≥ 750 ms) viene inviato al worker thread.
  4. Il worker thread fa: speaker verify → STT → controllo comandi speciali → ChatAgent.

Comandi vocali speciali:
  "Daniela non ascoltare ora"              → pausa ascolto
  "Daniela parla con me"                   → riprendi ascolto
  "Daniela allena il riconoscimento vocale" → avvia/aggiorna enrollment

Stato incerto (speaker similarity 0.55–0.75):
  Il server chiede "Stai parlando con me?" — il client risponde via confirm_yes/confirm_no.
"""
from __future__ import annotations

import asyncio
import collections
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from typing import Callable, Coroutine, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.voice.speaker_verifier import SpeakerVerifier
    from src.voice.vad_processor    import VADProcessor
    from src.voice.stt_engine       import STTEngine


# ── Costanti ──────────────────────────────────────────────────────────────────

SAMPLE_RATE        = 16000
FRAME_SAMPLES      = 480           # 30 ms a 16 kHz
FRAME_BYTES        = FRAME_SAMPLES * 2  # int16
PADDING_FRAMES     = 10            # 300 ms pre/post-speech — determina anche il trigger di fine parlato
MIN_SEG_SAMPLES    = int(SAMPLE_RATE * 0.3)   # minimo 300 ms per processare

ENROLL_DURATION_S  = 25            # secondi di parlato per un enrollment
MIN_ENROLL_SEGS    = 5             # segmenti minimi per l'enrollment

# Comandi vocali (normalizzati, minuscolo, senza punteggiatura)
_CMD_PAUSE  = ["daniela non ascoltare", "daniela non ascoltare ora"]
_CMD_RESUME = ["daniela parla con me", "daniela riprendi ad ascoltare"]
_CMD_ENROLL = [
    "daniela allena il riconoscimento vocale",
    "daniela allena riconoscimento vocale",
    "daniela aggiorna il riconoscimento vocale",
]
_CMD_CONFIRM_YES = [
    "sì", "si", "sono io", "sì sono io", "si sono io",
    "sto parlando con te", "sì sto parlando con te",
    "certo", "esatto", "sì esatto", "confermo",
]
_CMD_CONFIRM_NO  = [
    "no", "non sono io", "no non stavo",
    "non stavo parlando con te", "non ero io",
]


def _norm(text: str) -> str:
    return re.sub(r"[^a-zàèéìòùü ]", "", text.lower().strip())


def _matches(text: str, commands: list[str]) -> bool:
    # Aggiunge spazi attorno al testo per il controllo word-boundary
    # (es. "no" non deve matchare "non lo so")
    n = " " + _norm(text) + " "
    return any((" " + cmd + " ") in n for cmd in commands)


# ── Stato pipeline ─────────────────────────────────────────────────────────────

class PipelineState(Enum):
    ACTIVE     = auto()   # normale
    PAUSED     = auto()   # "Daniela non ascoltare" — ascolta solo il comando di ripresa
    ENROLLING  = auto()   # raccoglie audio per costruire/aggiornare il voice print
    CONFIRMING = auto()   # attende conferma "stai parlando con me?"


# ── Pipeline ──────────────────────────────────────────────────────────────────

class AudioPipeline:
    """
    Istanza per singola connessione client.
    I modelli pesanti (verifier, stt) vengono condivisi tra connessioni.
    """

    def __init__(
        self,
        verifier : "SpeakerVerifier",
        vad      : "VADProcessor",
        stt      : "STTEngine",
        loop     : asyncio.AbstractEventLoop,
        send_fn  : Callable[[dict], Coroutine],  # async def send(msg: dict)
        chat_fn  : Callable[[str],  Coroutine],  # async def on_text(text: str)
    ):
        self._verifier = verifier
        self._vad      = vad
        self._stt      = stt
        self._loop     = loop
        self._send_fn  = send_fn
        self._chat_fn  = chat_fn

        self._alive        : bool               = True
        # Quando True la pipeline scarta tutto l'audio in ingresso
        # (usato durante TTS per evitare che l'IA si auto-ascolti)
        self._deaf         : bool               = False

        # VAD streaming state
        self._ring         : collections.deque = collections.deque(maxlen=PADDING_FRAMES)
        self._voiced_f32   : list[np.ndarray]  = []
        self._triggered    : bool              = False
        self._leftover     : np.ndarray        = np.array([], dtype=np.float32)

        # Pipeline state (protetto da lock)
        self._state        : PipelineState       = PipelineState.ACTIVE
        self._lock         : threading.Lock      = threading.Lock()
        self._pending_text : Optional[str]       = None
        self._pending_seg  : Optional[np.ndarray] = None  # audio del segmento in attesa di conferma

        # Enrollment state
        self._enroll_segs      : list[np.ndarray] = []
        self._enroll_speech_s  : float            = 0.0

        # Worker
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")

    # ── Entry point (chiamato dal loop asyncio) ───────────────────────────────

    def set_deaf(self, deaf: bool) -> None:
        """Attiva/disattiva la modalità sorda (scarta audio in ingresso)."""
        self._deaf = deaf
        if deaf:
            # Svuota il buffer VAD per non processare audio accumulato
            self._voiced_f32.clear()
            self._ring.clear()
            self._triggered = False
            self._leftover  = np.array([], dtype=np.float32)

    def push_audio_chunk(self, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
        """
        Riceve un chunk PCM float32 mono dal client WebSocket.
        Esecuzione veloce: aggiorna solo il VAD state machine.
        Quando rileva fine di un segmento, lo invia al worker thread.
        """
        if self._deaf:
            return  # IA sta parlando — scarta
        if sr != SAMPLE_RATE:
            audio = _resample_f32(audio, sr, SAMPLE_RATE)

        combined       = np.concatenate([self._leftover, audio])
        n_frames       = len(combined) // FRAME_SAMPLES
        leftover_start = n_frames * FRAME_SAMPLES
        self._leftover = combined[leftover_start:] if leftover_start < len(combined) else np.array([], dtype=np.float32)

        for i in range(n_frames):
            frame = combined[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            self._feed_frame(frame)

    def handle_confirm_response(self, is_yes: bool) -> None:
        """Chiamato quando il client risponde alla richiesta di conferma speaker."""
        with self._lock:
            if self._state != PipelineState.CONFIRMING:
                return
            text           = self._pending_text
            seg            = self._pending_seg
            self._pending_text = None
            self._pending_seg  = None
            self._state    = PipelineState.ACTIVE

        if is_yes:
            # Aggiorna il voice print con questo segmento confermato
            if seg is not None:
                try:
                    self._verifier.update([seg], sr=SAMPLE_RATE)
                    print("[pipeline] Voice print aggiornato con segmento confermato")
                except Exception as exc:
                    print(f"[pipeline] Aggiornamento voice print fallito: {exc}")
            if text:
                self._schedule_chat(text)

    def start_enrollment(self) -> None:
        """Avvia l'enrollment (chiamabile anche da UI via messaggio WebSocket)."""
        with self._lock:
            self._state           = PipelineState.ENROLLING
            self._enroll_segs     = []
            self._enroll_speech_s = 0.0
        self._emit({
            "type":     "enroll_start",
            "duration": ENROLL_DURATION_S,
            "message":  f"Parla normalmente per circa {ENROLL_DURATION_S} secondi…",
        })
        print("[pipeline] Enrollment avviato")

    def shutdown(self) -> None:
        self._alive = False
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ── VAD state machine ─────────────────────────────────────────────────────

    def _feed_frame(self, frame_f32: np.ndarray) -> None:
        """Processa un singolo frame da 30 ms nel VAD state machine."""
        frame_i16 = (np.clip(frame_f32, -1.0, 1.0) * 32767).astype(np.int16)
        is_speech = self._vad.is_speech_frame(frame_i16.tobytes())

        if not self._triggered:
            self._ring.append((frame_f32, is_speech))
            num_voiced = sum(1 for _, s in self._ring if s)
            if num_voiced > 0.9 * len(self._ring):
                # Inizio parlato: includi tutto il ring come pre-padding
                self._triggered  = True
                self._voiced_f32 = [f for f, _ in self._ring]
                self._ring.clear()
        else:
            self._voiced_f32.append(frame_f32)
            self._ring.append((frame_f32, is_speech))
            num_unvoiced = sum(1 for _, s in self._ring if not s)
            if num_unvoiced > 0.9 * len(self._ring):
                # Fine parlato: includi ring come post-padding e finalizza
                frames = self._voiced_f32 + [f for f, _ in self._ring]
                self._voiced_f32 = []
                self._ring.clear()
                self._triggered  = False
                self._submit_segment(frames)

    def _submit_segment(self, frames: list[np.ndarray]) -> None:
        if not frames or not self._alive:
            return
        seg = np.concatenate(frames)
        if len(seg) < MIN_SEG_SAMPLES:
            return
        # NON catturare lo stato qui: il worker lo legge al momento dell'esecuzione
        # per evitare race condition con _finish_enrollment.
        try:
            self._executor.submit(self._process_segment, seg)
        except RuntimeError:
            pass  # executor già spento (shutdown durante disconnessione)

    # ── Worker (thread separato) ──────────────────────────────────────────────

    def _process_segment(self, seg: np.ndarray) -> None:
        # Legge lo stato con il lock al momento dell'esecuzione (non al submit)
        with self._lock:
            state = self._state

        try:
            if state == PipelineState.ENROLLING:
                self._handle_enroll_segment(seg)

            elif state == PipelineState.CONFIRMING:
                text = self._stt.transcribe(seg)
                print(f"[pipeline] confirm input: {text!r}")
                if _matches(text, _CMD_CONFIRM_YES):
                    self.handle_confirm_response(True)
                elif _matches(text, _CMD_CONFIRM_NO):
                    self.handle_confirm_response(False)

            elif state == PipelineState.PAUSED:
                text = self._stt.transcribe(seg)
                print(f"[pipeline] (pausa) {text!r}")
                if _matches(text, _CMD_RESUME):
                    with self._lock:
                        self._state = PipelineState.ACTIVE
                    self._emit({"type": "speaker_status", "state": "resumed"})
                    print("[pipeline] Ascolto ripreso")

            else:  # ACTIVE
                self._handle_active(seg)

        except Exception as exc:
            print(f"[pipeline] Errore worker: {exc}")

    def _handle_active(self, seg: np.ndarray) -> None:
        if not self._verifier.is_enrolled:
            # Nessun voice print → processa tutto senza verifica
            self._emit({"type": "stt_status", "state": "transcribing"})
            text = self._stt.transcribe(seg)
            self._emit({"type": "stt_status", "state": "idle"})
            if not text:
                return
            print(f"[pipeline] testo (no vp): {text!r}")
            self._emit({"type": "stt_text", "text": text})
            self._dispatch_text(text)
            return

        # ── Verify e transcribe in parallelo ─────────────────────────────────
        # STT parte subito: se la verifica fallisce scartiamo il testo, ma
        # non paghiamo il tempo di attesa sequenziale verify → transcribe.
        self._emit({"type": "stt_status", "state": "transcribing"})

        verify_result : list = []
        transcribe_result: list = []
        verify_done = threading.Event()

        def _do_verify():
            try:
                verify_result.append(self._verifier.verify(seg))
            except Exception as exc:
                verify_result.append(("error", 0.0))
                print(f"[pipeline] Errore verifica: {exc}")
            finally:
                verify_done.set()

        def _do_transcribe():
            try:
                transcribe_result.append(self._stt.transcribe(seg))
            except Exception as exc:
                transcribe_result.append("")
                print(f"[pipeline] Errore trascrizione: {exc}")

        t_verify     = threading.Thread(target=_do_verify,     daemon=True)
        t_transcribe = threading.Thread(target=_do_transcribe, daemon=True)
        t_verify.start()
        t_transcribe.start()

        # Aspetta la verifica (di solito più veloce di STT)
        verify_done.wait()
        verdict, score = verify_result[0]

        print(f"[pipeline] speaker={verdict} score={score:.2f}")
        self._emit({
            "type":    "speaker_result",
            "verdict": verdict,
            "score":   round(score, 2),
        })

        if verdict == "no_match":
            # Scarta immediatamente — il thread STT (daemon) finirà da solo
            # senza che dobbiamo aspettarlo. La pipeline è subito pronta.
            self._emit({"type": "stt_status", "state": "idle"})
            return

        # Auto-update voice print in background (non blocca)
        if verdict == "match":
            self._executor.submit(self._auto_update_voiceprint, seg)

        # Aspetta trascrizione (potrebbe già essere pronta)
        t_transcribe.join()
        self._emit({"type": "stt_status", "state": "idle"})
        text = transcribe_result[0] if transcribe_result else ""
        if not text:
            return

        print(f"[pipeline] testo: {text!r}")
        self._emit({"type": "stt_text", "text": text})

        if verdict == "uncertain":
            if _matches(text, _CMD_PAUSE):
                self._do_pause()
                return
            if _matches(text, _CMD_RESUME):
                return
            with self._lock:
                self._state        = PipelineState.CONFIRMING
                self._pending_text = text
                self._pending_seg  = seg
            self._emit({"type": "confirm_speaking", "transcript": text})
            return

        self._dispatch_text(text)

    def _auto_update_voiceprint(self, seg: np.ndarray) -> None:
        """
        Aggiorna il voice print con un segmento appena riconosciuto come match.
        Chiamato in background dal ThreadPoolExecutor — non blocca la pipeline.
        UPDATE_WEIGHT basso (0.10) per cambiamenti graduali e stabili.
        """
        try:
            self._verifier.update([seg], sr=SAMPLE_RATE)
        except Exception as exc:
            print(f"[pipeline] Auto-update voice print fallito: {exc}")

    def _dispatch_text(self, text: str) -> None:
        if _matches(text, _CMD_PAUSE):
            self._do_pause()
        elif _matches(text, _CMD_RESUME):
            pass  # già attivo
        elif _matches(text, _CMD_ENROLL):
            self.start_enrollment()
        else:
            self._schedule_chat(text)

    # ── Enrollment ────────────────────────────────────────────────────────────

    def _handle_enroll_segment(self, seg: np.ndarray) -> None:
        # Controlla e aggiorna lo stato sotto lock per evitare race condition
        with self._lock:
            if self._state != PipelineState.ENROLLING:
                return  # enrollment già terminato da un altro thread
            self._enroll_segs.append(seg)
            self._enroll_speech_s += len(seg) / SAMPLE_RATE
            speech_s = self._enroll_speech_s
            n_segs   = len(self._enroll_segs)

        pct = int(min(speech_s / ENROLL_DURATION_S * 100, 99))
        self._emit({"type": "enroll_progress", "pct": pct})
        print(f"[pipeline] enrollment {pct}% ({speech_s:.1f}s)")

        if speech_s >= ENROLL_DURATION_S and n_segs >= MIN_ENROLL_SEGS:
            self._finish_enrollment()

    def _finish_enrollment(self) -> None:
        # Acquisisce lock per leggere i segmenti e cambiare stato atomicamente.
        # Se lo stato non è più ENROLLING (un altro thread ci è arrivato prima), esce.
        with self._lock:
            if self._state != PipelineState.ENROLLING:
                return
            segs                  = self._enroll_segs[:]
            self._state           = PipelineState.ACTIVE
            self._enroll_segs     = []
            self._enroll_speech_s = 0.0

        is_update = self._verifier.is_enrolled
        try:
            if is_update:
                self._verifier.update(segs, sr=SAMPLE_RATE)
                msg = "Voice print aggiornato con successo!"
            else:
                self._verifier.enroll(segs, sr=SAMPLE_RATE)
                msg = "Riconoscimento vocale attivato! D'ora in poi rispondo solo a te."
        except Exception as exc:
            self._emit({"type": "enroll_error", "message": str(exc)})
            return

        self._emit({"type": "enroll_done", "message": msg})
        print(f"[pipeline] Enrollment completato: {msg}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _do_pause(self) -> None:
        with self._lock:
            self._state = PipelineState.PAUSED
        self._emit({"type": "speaker_status", "state": "paused"})
        print("[pipeline] Ascolto in pausa")

    def _emit(self, msg: dict) -> None:
        """Invia un messaggio al client WebSocket dal thread worker."""
        asyncio.run_coroutine_threadsafe(
            self._send_fn(msg), self._loop
        )

    def _schedule_chat(self, text: str) -> None:
        """Schedula la risposta del ChatAgent nel loop asyncio."""
        asyncio.run_coroutine_threadsafe(
            self._chat_fn(text), self._loop
        )


# ── Resampling utility ────────────────────────────────────────────────────────

def _resample_f32(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(src_sr, dst_sr)
        return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)
    except ImportError:
        ratio   = dst_sr / src_sr
        new_len = int(len(audio) * ratio)
        if new_len == 0:
            return np.array([], dtype=np.float32)
        indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
