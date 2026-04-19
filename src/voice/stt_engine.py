"""
STTEngine — trascrizione vocale locale con faster-whisper.

Il modello è caricato in modo lazy al primo utilizzo e condiviso tra thread
tramite un lock (faster-whisper non è thread-safe durante l'inferenza).
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np


class STTEngine:
    def __init__(
        self,
        model_size: str  = "small",
        language: str    = "it",
        device: str      = "auto",
    ):
        """
        model_size: "tiny" (39 MB), "base" (74 MB), "small" (244 MB, consigliato),
                    "medium" (769 MB), "large-v3" (1.5 GB)
        language:   codice lingua ISO 639-1 (es. "it", "en")
        device:     "auto" | "cpu" | "cuda"
        """
        self._model_size = model_size
        self._language   = language
        self._device     = device
        self._model      = None
        self._lock       = threading.Lock()

    # ── Lazy load ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ImportError(
                "faster-whisper non installato — esegui: pip install faster-whisper"
            ) from e

        device = self._device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        compute = "float16" if device == "cuda" else "int8"
        print(f"[stt] Caricamento modello Whisper '{self._model_size}' su {device}…")
        self._model = WhisperModel(
            self._model_size, device=device, compute_type=compute
        )
        print("[stt] Modello pronto.")

    # ── Trascrizione ──────────────────────────────────────────────────────────

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        """
        Trascrive audio float32 mono.
        audio: np.ndarray float32, range [-1.0, 1.0]
        sr:    sample rate dell'audio in ingresso
        """
        self._load()

        audio = audio.astype(np.float32)
        if sr != 16000:
            audio = _resample(audio, sr, 16000)

        with self._lock:
            # beam_size=1 (greedy) è ~3-5× più veloce con perdita trascurabile su
            # frasi brevi. vad_filter=False perché VADProcessor ha già ritagliato
            # il segmento a monte — un secondo pass introduce solo latenza.
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,
                vad_filter=False,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()

        return text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(src_sr, dst_sr)
        return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)
    except ImportError:
        ratio   = dst_sr / src_sr
        new_len = int(len(audio) * ratio)
        indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
