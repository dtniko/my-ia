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
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from typing import Callable, Coroutine, Optional, TYPE_CHECKING

import numpy as np

# Usa lo stesso logger di voice_server se già inizializzato, altrimenti crea
import logging as _logging
_timing_logger = _logging.getLogger("ltsia.timing")

def _tlog(msg: str) -> None:
    print(msg, flush=True)
    _timing_logger.debug(msg)

if TYPE_CHECKING:
    from src.voice.speaker_verifier import SpeakerVerifier
    from src.voice.vad_processor    import VADProcessor
    from src.voice.stt_engine       import STTEngine


# ── Costanti ──────────────────────────────────────────────────────────────────

SAMPLE_RATE        = 16000
FRAME_SAMPLES      = 480           # 30 ms a 16 kHz
FRAME_BYTES        = FRAME_SAMPLES * 2  # int16
PADDING_FRAMES     = 10            # 300 ms pre/post-speech — determina anche il trigger di fine parlato
MIN_SEG_SAMPLES    = int(SAMPLE_RATE * 0.4)   # minimo 400 ms per processare
MIN_RMS_ENERGY     = 0.004                    # gate energetico: sotto questa soglia il segmento è scartato

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

# Frasi di commiato/chiusura che terminano la sessione conversazionale.
# Dopo una di queste, Daniela torna a richiedere la wake word.
_DISMISSALS = [
    "ok grazie", "grazie", "grazie mille", "grazie daniela",
    "ho capito", "ok ho capito", "ho capito grazie", "capito",
    "capito grazie", "va bene", "ok va bene", "va bene grazie",
    "va bene daniela", "ok grazie daniela", "bene grazie",
    "d accordo", "perfetto grazie", "ottimo grazie",
    "ok ciao", "ciao daniela", "arrivederci", "arrivederci daniela",
    "a dopo", "a dopo daniela", "ci vediamo", "alla prossima",
]
# _matches già normalizza le varianti della wake word prima del confronto,
# quindi i dismissal sopra con "daniela" coprono anche "daniele", "danielle" ecc.
_MAX_DISMISSAL_WORDS = 8   # sopra questa soglia non è un commiato


# Varianti della wake word prodotte da Whisper per "Daniela"
_WAKE_VARIANTS = {
    "daniela", "daniele", "danielle", "daniella", "daniel",
    "dani ela", "danie la", "daniella",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-zàèéìòùü ]", "", text.lower().strip())


def _has_wake_word(text: str) -> bool:
    """True se il testo contiene una variante riconosciuta della wake word."""
    n = _norm(text)
    words = n.split()
    # Controlla parola singola
    for w in words:
        if w in _WAKE_VARIANTS:
            return True
    # Controlla bigram (es. "dani ela")
    for i in range(len(words) - 1):
        bigram = words[i] + " " + words[i + 1]
        if bigram in _WAKE_VARIANTS:
            return True
    return False


def _normalize_wake(text: str) -> str:
    """Sostituisce varianti della wake word con 'daniela' per i match comandi."""
    n = _norm(text)
    for v in _WAKE_VARIANTS - {"daniela"}:
        n = re.sub(r"\b" + re.escape(v) + r"\b", "daniela", n)
    return n


def _matches(text: str, commands: list[str]) -> bool:
    # Normalizza le varianti della wake word prima di confrontare
    n = " " + _normalize_wake(text) + " "
    return any((" " + cmd + " ") in n for cmd in commands)


def _is_dismissal(text: str) -> bool:
    """
    True se il testo è una frase di chiusura conversazione.
    Limite di parole: frasi lunghe non vengono mai trattate come commiato
    anche se contengono parole come "grazie" o "va bene".
    """
    n = _norm(text)
    if len(n.split()) > _MAX_DISMISSAL_WORDS:
        return False
    return _matches(text, _DISMISSALS)


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

        # Stato conversazionale: True = Daniela è già stata invocata,
        # le frasi successive non richiedono la wake word.
        # Torna False dopo una frase di commiato.
        self._in_conversation: bool             = False

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

        # Flag atomico: True mentre un _process_segment è in esecuzione.
        # Se arriva un nuovo segmento con STT già occupata, viene scartato
        # per evitare accodamento che degrada i tempi a decine di secondi.
        self._stt_busy     : bool                = False

        # Enrollment state
        self._enroll_segs      : list[np.ndarray] = []
        self._enroll_speech_s  : float            = 0.0

        # Worker
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")

        # Contatori per rate logging
        self._chunk_count   : int   = 0
        self._segment_count : int   = 0
        self._rate_window   : float = time.monotonic()

        # Timing end-to-end: t0 = quando il VAD chiude il segmento parlato
        self._t_vad_end     : float = 0.0

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

        self._chunk_count += 1
        now = time.monotonic()
        elapsed = now - self._rate_window
        if elapsed >= 10.0:
            chunk_rate = self._chunk_count / elapsed
            seg_rate   = self._segment_count / elapsed
            print(f"[pipeline] rate: {chunk_rate:.1f} chunk/s | {seg_rate:.2f} segmenti/s "
                  f"({self._chunk_count} chunk, {self._segment_count} seg in {elapsed:.0f}s)")
            self._chunk_count   = 0
            self._segment_count = 0
            self._rate_window   = now

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
                self._dispatch_text(text)

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
        self._segment_count += 1
        self._t_vad_end = time.monotonic()
        dur_ms = len(seg) / SAMPLE_RATE * 1000
        rms    = float(np.sqrt(np.mean(seg ** 2)))
        if rms < MIN_RMS_ENERGY:
            print(f"[pipeline] segmento scartato (energia troppo bassa: rms={rms:.4f} < {MIN_RMS_ENERGY})")
            return
        if self._stt_busy:
            print(f"[pipeline] segmento scartato (STT occupata): {dur_ms:.0f}ms rms={rms:.4f}")
            return
        print(f"[pipeline] segmento rilevato: {dur_ms:.0f}ms rms={rms:.4f}")
        # NON catturare lo stato qui: il worker lo legge al momento dell'esecuzione
        # per evitare race condition con _finish_enrollment.
        try:
            self._stt_busy = True
            self._executor.submit(self._process_segment, seg)
        except RuntimeError:
            self._stt_busy = False

    # ── Worker (thread separato) ──────────────────────────────────────────────

    def _process_segment(self, seg: np.ndarray) -> None:
        # Legge lo stato con il lock al momento dell'esecuzione (non al submit)
        with self._lock:
            state = self._state

        t0 = time.monotonic()
        try:
            if state == PipelineState.ENROLLING:
                self._handle_enroll_segment(seg)

            elif state == PipelineState.CONFIRMING:
                t_stt = time.monotonic()
                text  = self._stt.transcribe(seg)
                print(f"[pipeline] confirm STT: {(time.monotonic()-t_stt)*1000:.0f}ms → {text!r}")
                if _matches(text, _CMD_CONFIRM_YES):
                    self.handle_confirm_response(True)
                elif _matches(text, _CMD_CONFIRM_NO):
                    self.handle_confirm_response(False)

            elif state == PipelineState.PAUSED:
                t_stt = time.monotonic()
                text  = self._stt.transcribe(seg)
                print(f"[pipeline] (pausa) STT: {(time.monotonic()-t_stt)*1000:.0f}ms → {text!r}")
                if _matches(text, _CMD_RESUME):
                    with self._lock:
                        self._state = PipelineState.ACTIVE
                    self._emit({"type": "speaker_status", "state": "resumed"})
                    print("[pipeline] Ascolto ripreso")

            else:  # ACTIVE
                self._handle_active(seg)

        except Exception as exc:
            print(f"[pipeline] Errore worker: {exc}")
        finally:
            self._stt_busy = False
            elapsed = (time.monotonic() - t0) * 1000
            if elapsed > 2000:
                print(f"[pipeline] ⚠ segmento lento: {elapsed:.0f}ms (stato={state.name})")

    def _handle_active(self, seg: np.ndarray) -> None:
        t0    = self._t_vad_end  # riferimento comune per tutti i delta
        dur_s = len(seg) / SAMPLE_RATE

        def _dt(label: str) -> None:
            ms = (time.monotonic() - t0) * 1000
            _tlog(f"[timing] +{ms:6.0f}ms  {label}")

        _dt("worker STT avviato")

        if not self._verifier.is_enrolled:
            # Nessun voice print → processa tutto senza verifica
            self._emit({"type": "stt_status", "state": "transcribing"})
            t_stt = time.monotonic()
            text  = self._stt.transcribe(seg)
            stt_ms = (time.monotonic() - t_stt) * 1000
            self._emit({"type": "stt_status", "state": "idle"})
            _dt(f"STT completato ({stt_ms:.0f}ms, audio {dur_s:.2f}s) → {text!r}")
            if not text:
                return
            self._emit({"type": "stt_text", "text": text})
            self._dispatch_text(text)
            _dt("dispatch_text chiamato")
            return

        # ── Verify e transcribe in parallelo ─────────────────────────────────
        # STT parte subito: se la verifica fallisce scartiamo il testo, ma
        # non paghiamo il tempo di attesa sequenziale verify → transcribe.
        self._emit({"type": "stt_status", "state": "transcribing"})

        verify_result    : list  = []
        transcribe_result: list  = []
        verify_done              = threading.Event()
        t_verify_start           = time.monotonic()
        t_stt_start              = time.monotonic()

        def _do_verify():
            try:
                verify_result.append(self._verifier.verify(seg))
            except Exception as exc:
                verify_result.append(("error", 0.0))
                print(f"[pipeline] Errore verifica: {exc}")
            finally:
                verify_done.set()
                _dt(f"verify completato ({(time.monotonic()-t_verify_start)*1000:.0f}ms)")

        def _do_transcribe():
            try:
                transcribe_result.append(self._stt.transcribe(seg))
            except Exception as exc:
                transcribe_result.append("")
                print(f"[pipeline] Errore trascrizione: {exc}")
            _dt(f"STT completato ({(time.monotonic()-t_stt_start)*1000:.0f}ms, audio {dur_s:.2f}s)")

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
        _dt("STT join completato")
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
        _dt("dispatch_text → chat schedulato")

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
        # Comandi di sistema — precedenza assoluta
        if _matches(text, _CMD_PAUSE):
            self._do_pause()
            return
        if _matches(text, _CMD_RESUME):
            return   # già attivo
        if _matches(text, _CMD_ENROLL):
            self.start_enrollment()
            return

        # Logica wake word + stato conversazione
        has_wake = _has_wake_word(text)
        is_bye   = _is_dismissal(text)

        if is_bye and self._in_conversation:
            # Commiato: chiudi la sessione senza rispondere
            self._in_conversation = False
            print("[pipeline] Sessione conversazione chiusa (commiato)")
            return

        if self._in_conversation:
            # Già in conversazione: risponde sempre (no wake word richiesta)
            self._schedule_chat(text)
        elif has_wake:
            # Prima invocazione con wake word: apri sessione
            self._in_conversation = True
            print(f"[pipeline] Wake word rilevata in: {text!r}")
            self._schedule_chat(text)
        else:
            # Nessuna wake word e non in conversazione: ignora
            print(f"[pipeline] Ignorato (no wake word, no sessione): {text!r}")

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
        if self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send_fn(msg), self._loop)
        except RuntimeError:
            pass

    def _schedule_chat(self, text: str) -> None:
        """Schedula la risposta del ChatAgent nel loop asyncio."""
        if self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._chat_fn(text), self._loop)
        except RuntimeError:
            pass


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
