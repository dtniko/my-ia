"""
TelegramBot — interfaccia Telegram per LTSIA.

Supporta:
  - messaggi di testo  → risposta testo
  - messaggi vocali    → trascrizione Whisper → risposta testo + voice note OGG

Usa long-polling puro (requests) senza dipendenze extra (python-telegram-bot).
Avviare come thread daemon tramite Application o con `python -m src.telegram.telegram_bot`.

Configurazione in ltsia.ini (sezione [ltsia]):
  telegram_token = <token BotFather>
  telegram_voice_reply = true          # rispondi con voce se il messaggio era vocale

Oppure env var LTSIA_TELEGRAM_TOKEN.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Optional

import requests

if TYPE_CHECKING:
    from src.agents.chat_agent import ChatAgent
    from src.config import Config

log = logging.getLogger("ltsia.telegram")


# ── Costanti Telegram API ────────────────────────────────────────────────────

_API = "https://api.telegram.org/bot{token}/{method}"
_FILE_URL = "https://api.telegram.org/file/bot{token}/{file_path}"
_POLL_TIMEOUT = 30          # secondi di long-polling
_CONNECT_TIMEOUT = 10       # timeout connessione


# ── Helpers HTTP ─────────────────────────────────────────────────────────────

def _api(token: str, method: str, **params) -> dict:
    url = _API.format(token=token, method=method)
    r = requests.post(url, json=params, timeout=(_CONNECT_TIMEOUT, _POLL_TIMEOUT + 5))
    r.raise_for_status()
    return r.json()


def _download(token: str, file_id: str) -> bytes:
    """Scarica un file da Telegram e ritorna il contenuto grezzo."""
    info = _api(token, "getFile", file_id=file_id)
    if not info.get("ok"):
        raise RuntimeError(f"getFile fallito: {info}")
    file_path = info["result"]["file_path"]
    url = _FILE_URL.format(token=token, file_path=file_path)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


# ── STT (Whisper) ─────────────────────────────────────────────────────────────

def _transcribe_ogg(audio_bytes: bytes, language: str = "it") -> str:
    """
    Converte OGG/OPUS → WAV con ffmpeg, poi trascrive con faster-whisper.
    Ritorna il testo trascritto o stringa vuota in caso di errore.
    """
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper non installato — STT non disponibile")
        return ""

    # OGG → PCM float32 mono 16kHz via ffmpeg
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "quiet",
                "-i", "pipe:0",
                "-ar", "16000", "-ac", "1", "-f", "f32le", "pipe:1",
            ],
            input=audio_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            log.warning("ffmpeg fallito durante conversione audio")
            return ""
        pcm = np.frombuffer(proc.stdout, dtype=np.float32)
    except Exception as e:
        log.warning(f"Errore conversione audio: {e}")
        return ""

    # Trascrizione
    try:
        # Usa modello leggero; caricato lazy a livello di modulo
        model = _get_whisper_model()
        segments, _ = model.transcribe(pcm, language=language, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
        return text
    except Exception as e:
        log.warning(f"Errore Whisper: {e}")
        return ""


_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            log.info(f"Caricamento Whisper 'small' su {device}…")
            _whisper_model = WhisperModel("small", device=device, compute_type=compute)
            log.info("Whisper pronto.")
    return _whisper_model


# ── TTS → OGG OPUS ───────────────────────────────────────────────────────────

def _tts_to_ogg(text: str, voice: str, rate: str = "+0%") -> bytes | None:
    """
    Genera audio edge-tts in MP3 e lo converte in OGG OPUS per Telegram.
    Ritorna bytes OGG o None se fallisce.
    """
    import shutil

    if not text or not voice:
        return None
    if not shutil.which("edge-tts"):
        log.warning("edge-tts non trovato — nessuna risposta vocale")
        return None
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg non trovato — nessuna conversione OGG")
        return None

    tmp_mp3 = None
    tmp_ogg = None
    try:
        tmp_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_mp3.close()
        tmp_ogg = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        tmp_ogg.close()

        # edge-tts → MP3
        r = subprocess.run(
            ["edge-tts", "--voice", voice, "--rate", rate, "--text", text, "--write-media", tmp_mp3.name],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0 or not os.path.getsize(tmp_mp3.name):
            return None

        # MP3 → OGG OPUS (Telegram vuole OPUS per voice notes)
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "quiet", "-i", tmp_mp3.name,
             "-c:a", "libopus", "-b:a", "32k", tmp_ogg.name],
            capture_output=True, timeout=30,
        )
        if r2.returncode != 0 or not os.path.getsize(tmp_ogg.name):
            return None

        with open(tmp_ogg.name, "rb") as f:
            return f.read()
    except Exception as e:
        log.warning(f"TTS OGG fallito: {e}")
        return None
    finally:
        for p in [tmp_mp3, tmp_ogg]:
            if p:
                try:
                    os.unlink(p.name)
                except Exception:
                    pass


# ── TelegramBot ───────────────────────────────────────────────────────────────

class TelegramBot:
    """
    Long-polling bot Telegram che usa il ChatAgent di LTSIA.

    Parametri
    ---------
    token           : token del bot (BotFather)
    chat_agent      : istanza ChatAgent già inizializzata
    config          : Config LTSIA (per TTS voice/rate)
    voice_reply     : se True, risponde con voice note quando il messaggio è vocale
    language        : lingua STT (default "it")
    allowed_chat_ids: lista di chat_id autorizzati; se vuota, accetta tutti
    """

    def __init__(
        self,
        token: str,
        chat_agent: "ChatAgent",
        config: "Config",
        voice_reply: bool = True,
        language: str = "it",
        allowed_chat_ids: list[int] | None = None,
    ):
        self.token = token
        self.chat_agent = chat_agent
        self.config = config
        self.voice_reply = voice_reply
        self.language = language
        self.allowed_chat_ids: list[int] = allowed_chat_ids or []
        self._offset = 0
        self._stop_event = threading.Event()

    # ── Polling ───────────────────────────────────────────────────────────────

    def run(self):
        """Blocca il thread corrente con loop di polling."""
        log.info("TelegramBot avviato — in ascolto aggiornamenti…")
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates()
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    self._dispatch(upd)
            except requests.exceptions.ConnectionError:
                log.warning("Telegram non raggiungibile — riprovo in 10s")
                time.sleep(10)
            except Exception as e:
                log.error(f"Errore polling: {e}")
                time.sleep(5)

    def start_background(self) -> threading.Thread:
        """Avvia il bot in un thread daemon. Ritorna il thread."""
        t = threading.Thread(target=self.run, daemon=True, name="telegram-bot")
        t.start()
        return t

    def stop(self):
        self._stop_event.set()

    # ── Ricezione aggiornamenti ───────────────────────────────────────────────

    def _get_updates(self) -> list[dict]:
        data = _api(
            self.token, "getUpdates",
            offset=self._offset,
            timeout=_POLL_TIMEOUT,
            allowed_updates=["message"],
        )
        if not data.get("ok"):
            return []
        return data.get("result", [])

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def _dispatch(self, update: dict):
        msg = update.get("message")
        if not msg:
            return

        chat_id = msg["chat"]["id"]

        # Verifica lista bianca
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            log.info(f"Messaggio da chat_id {chat_id} non autorizzato — ignorato")
            return

        is_voice = False

        # Testo diretto
        if "text" in msg:
            user_text = msg["text"].strip()

        # Voice note o audio
        elif "voice" in msg or "audio" in msg:
            is_voice = True
            file_id = (msg.get("voice") or msg.get("audio"))["file_id"]
            user_text = self._handle_voice_input(chat_id, file_id)
            if not user_text:
                return  # errore già notificato all'utente

        else:
            # Tipo non gestito (foto, documenti, ecc.)
            self._send_text(chat_id, "⚠️ Tipo di messaggio non supportato. Inviami testo o messaggi vocali.")
            return

        if not user_text:
            return

        log.info(f"[{chat_id}] Input: {user_text!r}")

        # Invia "typing…" mentre elabora
        self._send_action(chat_id, "typing")

        # ChatAgent
        try:
            response = self.chat_agent.chat(user_text)
        except Exception as e:
            log.error(f"Errore ChatAgent: {e}")
            self._send_text(chat_id, f"❌ Errore interno: {e}")
            return

        if not response:
            response = "(nessuna risposta)"

        log.info(f"[{chat_id}] Risposta: {response[:80]}…")

        # Risposta testuale sempre inviata
        self._send_text(chat_id, response)

        # Risposta vocale se il messaggio era vocale
        if is_voice and self.voice_reply:
            self._send_voice_reply(chat_id, response)

    # ── Input vocale ──────────────────────────────────────────────────────────

    def _handle_voice_input(self, chat_id: int, file_id: str) -> str:
        """Scarica audio da Telegram e trascrive. Ritorna testo o '' se fallisce."""
        try:
            self._send_action(chat_id, "typing")
            audio_bytes = _download(self.token, file_id)
        except Exception as e:
            log.error(f"Download audio fallito: {e}")
            self._send_text(chat_id, "❌ Impossibile scaricare il messaggio vocale.")
            return ""

        text = _transcribe_ogg(audio_bytes, language=self.language)
        if not text:
            self._send_text(chat_id, "❌ Non sono riuscito a trascrivere il messaggio vocale.")
            return ""

        # Mostra la trascrizione all'utente come conferma
        self._send_text(chat_id, f"🎙️ _{text}_", parse_mode="Markdown")
        return text

    # ── Risposta vocale ───────────────────────────────────────────────────────

    def _send_voice_reply(self, chat_id: int, text: str):
        """Genera TTS e invia come voice note Telegram."""
        from src.voice.tts import resolve_tts_voice

        voice_name = resolve_tts_voice(self.config.tts_voice)
        if not voice_name:
            return  # TTS non disponibile

        self._send_action(chat_id, "record_voice")
        ogg_bytes = _tts_to_ogg(text, voice_name, self.config.tts_rate)
        if not ogg_bytes:
            return

        try:
            url = _API.format(token=self.token, method="sendVoice")
            requests.post(
                url,
                data={"chat_id": chat_id},
                files={"voice": ("risposta.ogg", io.BytesIO(ogg_bytes), "audio/ogg")},
                timeout=60,
            ).raise_for_status()
        except Exception as e:
            log.warning(f"Invio voice note fallito: {e}")

    # ── Invio messaggi ────────────────────────────────────────────────────────

    def _send_text(self, chat_id: int, text: str, parse_mode: str = ""):
        """Invia messaggio di testo, spezza automaticamente se > 4096 caratteri."""
        limit = 4096
        chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
        for chunk in chunks:
            try:
                params: dict = {"chat_id": chat_id, "text": chunk}
                if parse_mode:
                    params["parse_mode"] = parse_mode
                _api(self.token, "sendMessage", **params)
            except Exception as e:
                log.error(f"sendMessage fallito: {e}")

    def _send_action(self, chat_id: int, action: str):
        try:
            _api(self.token, "sendChatAction", chat_id=chat_id, action=action)
        except Exception:
            pass


# ── Avvio standalone ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = os.environ.get("LTSIA_TELEGRAM_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not token:
        print("Uso: LTSIA_TELEGRAM_TOKEN=<token> python -m src.telegram.telegram_bot")
        sys.exit(1)

    from src.config import Config
    from src.application import Application

    cfg = Config.load()
    app = Application(cfg)
    bot = TelegramBot(token=token, chat_agent=app.chat_agent, config=cfg)
    bot.run()
