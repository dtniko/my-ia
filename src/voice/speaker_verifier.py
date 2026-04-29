"""
SpeakerVerifier — voice print con ECAPA-TDNN (speechbrain).

Salva il voice print in ~/.ltsia/voice_print_ecapa.npy.
Verifica la similarità coseno tra voce in ingresso e print salvato.

Richiede: speechbrain==0.5.16, torch<=2.3, huggingface_hub
"""
from __future__ import annotations

# ── Monkey-patch: speechbrain 0.5.16 usa use_auth_token rimosso da hf_hub ──
# Intercetta anche il 404 su custom.py (non esiste nel repo spkrec-ecapa-voxceleb)
import huggingface_hub as _hf
_orig_hf_download = _hf.hf_hub_download
def _hf_compat(*a, use_auth_token=None, **kw):
    if use_auth_token is not None:
        kw.setdefault("token", use_auth_token)
    try:
        return _orig_hf_download(*a, **kw)
    except Exception as _e:
        filename = a[1] if len(a) > 1 else kw.get("filename", "")
        if "custom.py" in str(filename) and ("404" in str(_e) or "Entry Not Found" in str(_e)):
            # speechbrain.pretrained.fetching cattura HTTPError con "404 Client Error"
            # e lo converte in ValueError, che interfaces.py ignora per custom.py
            from requests.exceptions import HTTPError
            raise HTTPError("404 Client Error: custom.py not in repo") from _e
        raise
_hf.hf_hub_download = _hf_compat
# ────────────────────────────────────────────────────────────────────────────

import threading
from pathlib import Path
from typing import Optional

import numpy as np

VOICEPRINT_PATH     = Path.home() / ".ltsia" / "voice_print_ecapa.npy"
MODEL_DIR           = str(Path.home() / ".ltsia" / "models" / "spkrec-ecapa")
MODEL_SOURCE        = "speechbrain/spkrec-ecapa-voxceleb"

# Soglie calibrate su ECAPA-TDNN (auto-update migliora i punteggi nel tempo):
#   utente registrato → 0.40–0.60+ (cresce con l'uso)
#   altri parlanti    → <0.10
THRESHOLD_MATCH     = 0.50   # ≥ questo → match
THRESHOLD_UNCERTAIN = 0.30   # tra i due → incerto, chiedi conferma

UPDATE_WEIGHT = 0.10  # 10% nuovo campione + 90% esistente — aggiornamento graduale


class SpeakerVerifier:
    def __init__(self, voiceprint_path: Path = VOICEPRINT_PATH):
        self._path    = voiceprint_path
        self._model   = None
        self._lock    = threading.Lock()
        self._vp: Optional[np.ndarray] = None  # voice print (192-dim, normalizzato)

    # ── Proprietà ─────────────────────────────────────────────────────────────

    @property
    def is_enrolled(self) -> bool:
        if self._vp is not None:
            return True
        if self._path.exists():
            self._vp = np.load(str(self._path))
            return True
        return False

    # ── Caricamento modello ───────────────────────────────────────────────────

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            import speechbrain.pretrained as sb_pre
            self._model = sb_pre.EncoderClassifier.from_hparams(
                source=MODEL_SOURCE,
                savedir=MODEL_DIR,
                run_opts={"device": "cpu"},
            )
            self._model.eval()
        except ImportError as e:
            raise ImportError(
                "speechbrain non installato — esegui: pip install speechbrain==0.5.16"
            ) from e

    # ── Enrollment ────────────────────────────────────────────────────────────

    def enroll(self, segments: list[np.ndarray], sr: int = 16000) -> None:
        """
        Crea un voice print dai segmenti audio (float32 mono).
        Sovrascrive il voice print esistente.
        """
        self._load_model()
        embeddings = self._embed_segments(segments, sr)
        if not embeddings:
            raise ValueError("Nessun segmento audio valido per l'enrollment")
        vp = np.mean(embeddings, axis=0)
        vp = vp / (np.linalg.norm(vp) + 1e-9)
        self._save(vp)

    def update(self, segments: list[np.ndarray], sr: int = 16000) -> None:
        """
        Aggiorna il voice print esistente con nuovi campioni (media pesata).
        Se non c'è voice print, lo crea.
        """
        if not self.is_enrolled:
            self.enroll(segments, sr)
            return

        self._load_model()
        embeddings = self._embed_segments(segments, sr)
        if not embeddings:
            return  # segmenti troppo corti — ignora silenziosamente

        new_emb = np.mean(embeddings, axis=0)
        new_emb = new_emb / (np.linalg.norm(new_emb) + 1e-9)

        with self._lock:
            updated = (1.0 - UPDATE_WEIGHT) * self._vp + UPDATE_WEIGHT * new_emb
            updated = updated / (np.linalg.norm(updated) + 1e-9)
        self._save(updated)

    def reset(self) -> None:
        """Elimina il voice print salvato e reimposta lo stato in memoria."""
        with self._lock:
            self._vp = None
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass

    # ── Verifica ──────────────────────────────────────────────────────────────

    def verify(self, audio: np.ndarray, sr: int = 16000) -> tuple[str, float]:
        """
        Verifica se l'audio appartiene all'utente registrato.

        Returns:
            (verdict, score) dove verdict è:
              "match"         → score ≥ THRESHOLD_MATCH
              "uncertain"     → THRESHOLD_UNCERTAIN ≤ score < THRESHOLD_MATCH
              "no_match"      → score < THRESHOLD_UNCERTAIN
              "no_voiceprint" → nessun voice print registrato
              "too_short"     → segmento troppo corto (< 0.5s)
              "error"         → errore durante l'embedding
        """
        if not self.is_enrolled:
            return "no_voiceprint", 0.0

        if len(audio) < sr * 0.5:
            return "too_short", 0.0

        self._load_model()
        try:
            emb = self._embed_audio(audio, sr)
        except Exception as exc:
            print(f"[verifier] Errore embedding: {exc}")
            return "error", 0.0

        with self._lock:
            vp = self._vp

        score = float(np.dot(emb, vp) / (np.linalg.norm(emb) * np.linalg.norm(vp) + 1e-9))

        if score >= THRESHOLD_MATCH:
            verdict = "match"
        elif score >= THRESHOLD_UNCERTAIN:
            verdict = "uncertain"
        else:
            verdict = "no_match"

        return verdict, score

    # ── Helpers privati ───────────────────────────────────────────────────────

    def _embed_audio(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Calcola embedding ECAPA-TDNN per un singolo array audio."""
        import torch
        wav = self._prepare_wav(audio, sr)
        wav_tensor = torch.tensor(wav).unsqueeze(0)  # (1, T)
        with torch.no_grad():
            emb = self._model.encode_batch(wav_tensor)  # (1, 1, 192)
        return emb.squeeze().cpu().numpy()

    def _embed_segments(self, segments: list[np.ndarray], sr: int) -> list[np.ndarray]:
        embeddings = []
        for seg in segments:
            if len(seg) < sr * 0.5:
                continue
            try:
                emb = self._embed_audio(seg, sr)
                embeddings.append(emb)
            except Exception as exc:
                print(f"[verifier] Segmento ignorato: {exc}")
        return embeddings

    def _prepare_wav(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """
        Converte in float32 mono normalizzato a 16kHz.
        ECAPA-TDNN di speechbrain si aspetta audio a 16kHz.
        """
        wav = audio.astype(np.float32)

        # Ricampiona se necessario
        if sr != 16000:
            try:
                from scipy.signal import resample_poly
                from math import gcd
                g = gcd(16000, sr)
                wav = resample_poly(wav, 16000 // g, sr // g)
            except ImportError:
                # fallback lineare (meno preciso ma funziona)
                target_len = int(len(wav) * 16000 / sr)
                wav = np.interp(
                    np.linspace(0, len(wav) - 1, target_len),
                    np.arange(len(wav)),
                    wav,
                )

        # Normalizza picco
        peak = np.max(np.abs(wav))
        if peak > 0:
            wav = wav / peak

        return wav

    def _save(self, vp: np.ndarray) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        np.save(str(self._path), vp)
        with self._lock:
            self._vp = vp
