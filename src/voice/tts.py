"""Wrapper per edge-tts — generazione audio MP3 lato server."""
from __future__ import annotations
import base64
import os
import re
import shutil
import subprocess
import tempfile

# Rimuove emoji e simboli grafici dal testo prima della sintesi vocale
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FFFF"   # emoji estesi (emoticons, simboli, bandiere, trasporto…)
    "\U00002600-\U000026FF"   # simboli vari (sole, luna, stelle…)
    "\U00002700-\U000027BF"   # dingbats
    "️"                  # variation selector (rende emoji i caratteri precedenti)
    "‍"                  # zero-width joiner
    "⃣"                  # combining enclosing keycap
    "]+",
    re.UNICODE,
)


def resolve_tts_voice(tts_voice: str) -> str:
    """
    Ritorna il nome della voce edge-tts da usare, o '' se disabilitata/non installata.
      tts_voice == 'disabled'   → disabilitato
      tts_voice == ''           → usa it-IT-IsabellaNeural se edge-tts è presente
      tts_voice == 'it-IT-...'  → usa quella voce
    """
    if tts_voice.strip().lower() == "disabled":
        return ""

    voice = tts_voice.strip() if tts_voice.strip() else "it-IT-IsabellaNeural"

    # Cerca edge-tts nel PATH
    if shutil.which("edge-tts"):
        return voice

    # Prova path comuni (pip install --user su macOS/Linux)
    home = os.environ.get("HOME", "")
    for p in [
        "/usr/local/bin/edge-tts",
        "/usr/bin/edge-tts",
        f"{home}/.local/bin/edge-tts",
        f"{home}/Library/Python/3.11/bin/edge-tts",
        f"{home}/Library/Python/3.12/bin/edge-tts",
        f"{home}/Library/Python/3.13/bin/edge-tts",
    ]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return voice

    return ""


def generate_tts_audio(text: str, voice: str, rate: str = "+0%") -> str:
    """
    Genera audio MP3 con edge-tts e ritorna il contenuto come stringa base64.
    Ritorna '' in caso di errore o se edge-tts non è disponibile.
    """
    if not text or not voice:
        return ""

    text = _EMOJI_RE.sub("", text).strip()
    if not text:
        return ""

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(tmp_fd)

    try:
        result = subprocess.run(
            [
                "edge-tts",
                "--voice", voice,
                "--rate",  rate,
                "--text",  text,
                "--write-media", tmp_path,
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            return ""

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return ""

        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    except Exception:
        return ""

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
