#!/usr/bin/env python3
"""
Benchmark voce per Raspberry Pi 5.

Testa:
  1. Installazione dipendenze (faster-whisper, webrtcvad, speechbrain)
  2. Latenza STT con modello tiny su audio sintetico
  3. Speaker recognition: embedding + latenza (se speechbrain disponibile)

Uso:
  python3 tools/test_voice_rpi.py
  python3 tools/test_voice_rpi.py --model tiny   # default
  python3 tools/test_voice_rpi.py --model base
  python3 tools/test_voice_rpi.py --stt-only      # salta speaker recognition
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np


def _header(title: str) -> None:
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print('─'*50)


def _ok(msg: str)   -> None: print(f"  [OK]  {msg}")
def _warn(msg: str) -> None: print(f"  [!]   {msg}")
def _fail(msg: str) -> None: print(f"  [ERR] {msg}")


# ── Controllo dipendenze ──────────────────────────────────────────────────────

def check_deps() -> dict[str, bool]:
    _header("1. Dipendenze")
    results = {}

    for pkg, import_name in [
        ("faster-whisper",  "faster_whisper"),
        ("webrtcvad",       "webrtcvad"),
        ("numpy",           "numpy"),
        ("scipy",           "scipy"),
        ("speechbrain",     "speechbrain"),
        ("torch",           "torch"),
    ]:
        try:
            __import__(import_name)
            _ok(pkg)
            results[pkg] = True
        except ImportError:
            _warn(f"{pkg} non installato")
            results[pkg] = False

    return results


# ── Crea audio sintetico ──────────────────────────────────────────────────────

def _make_speech_like(duration_s: float = 2.0, sr: int = 16000) -> np.ndarray:
    """
    Audio speech-like: burst di frequenze vocali (80-3400Hz) con inviluppo
    simile al parlato (ampiezza variabile). Non è parlato reale ma evita
    gli hallucination loop di Whisper su onde sinusoidali pure.
    """
    rng = np.random.default_rng(42)
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Fondamentale vocale + armoniche
    audio = (
        0.3 * np.sin(2 * np.pi * 150 * t) +   # fondamentale
        0.2 * np.sin(2 * np.pi * 300 * t) +   # 2° armonica
        0.1 * np.sin(2 * np.pi * 600 * t) +   # 4° armonica
        0.05 * rng.standard_normal(n)          # rumore di fondo
    )
    # Inviluppo tipo parlato (burst da 200ms separati da pause)
    envelope = np.zeros(n)
    for start in range(0, n - sr // 5, sr // 3):
        end = min(start + sr // 5, n)
        envelope[start:end] = 1.0
    audio = (audio * envelope * 0.5).astype(np.float32)
    return audio


def _make_noise(duration_s: float = 2.0, sr: int = 16000) -> np.ndarray:
    rng = np.random.default_rng(42)
    return (rng.standard_normal(int(sr * duration_s)) * 0.005).astype(np.float32)


# ── STT benchmark ─────────────────────────────────────────────────────────────

def benchmark_stt(model_size: str = "tiny") -> None:
    _header(f"2. STT — modello '{model_size}'")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        _fail("faster-whisper non installato — pip install faster-whisper")
        return

    print(f"  Caricamento modello '{model_size}' su CPU (int8)…")
    t0 = time.monotonic()
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4, num_workers=1)
    load_ms = (time.monotonic() - t0) * 1000
    _ok(f"Modello caricato in {load_ms:.0f}ms")

    # vad_filter=False: in produzione webrtcvad filtra l'audio upstream.
    # max_new_tokens limita l'output per evitare hallucination loop su audio sintetico.
    transcribe_kwargs = dict(language="it", beam_size=1, vad_filter=False,
                             condition_on_previous_text=False, max_new_tokens=50)

    durations = [1.0, 2.0, 3.0, 5.0]
    print(f"\n  {'Audio':>8}  {'Latenza':>10}  {'RTF':>8}  {'Testo'}")
    print(f"  {'─'*7}  {'─'*10}  {'─'*8}  {'─'*20}")

    for dur in durations:
        audio = _make_speech_like(dur)
        t0 = time.monotonic()
        segs, _ = model.transcribe(audio, **transcribe_kwargs)
        text = " ".join(s.text.strip() for s in segs).strip() or "(silenzio)"
        lat_ms = (time.monotonic() - t0) * 1000
        rtf = lat_ms / (dur * 1000)
        marker = "" if rtf < 1.0 else (" OK" if rtf < 2.0 else " LENTO")
        print(f"  {dur:>7.1f}s  {lat_ms:>9.0f}ms  {rtf:>7.2f}x{marker}")

    # Testa con rumore basso (deve essere scartato velocemente)
    audio = _make_noise(2.0)
    t0 = time.monotonic()
    segs, _ = model.transcribe(audio, **transcribe_kwargs)
    lat_ms = (time.monotonic() - t0) * 1000
    _ok(f"Rumore basso 2s → {lat_ms:.0f}ms (VAD lo scarta)")

    print(f"\n  RTF < 1.0x = elabora più veloce del parlato (ideale)")
    print(f"  RTF 1-2x  = con VAD la pipeline resta fluida (accettabile su RPi5)")
    print(f"  RTF > 2x  = considera modello tiny o riduzione cpu_threads")


# ── Speaker recognition benchmark ─────────────────────────────────────────────

def benchmark_speaker() -> None:
    _header("3. Speaker Recognition — ECAPA-TDNN (speechbrain)")

    try:
        import torch
    except ImportError:
        _warn("torch non installato — speaker recognition non disponibile")
        print("  Installa con: pip install torch --index-url https://download.pytorch.org/whl/cpu")
        return

    try:
        import speechbrain
    except ImportError:
        _warn("speechbrain non installato — pip install speechbrain==0.5.16")
        return

    print("  Caricamento modello ECAPA-TDNN da HuggingFace (solo primo avvio)…")
    try:
        # monkey-patch per compatibilità hf_hub
        import huggingface_hub as _hf
        _orig = _hf.hf_hub_download
        def _compat(*a, use_auth_token=None, **kw):
            if use_auth_token is not None:
                kw.setdefault("token", use_auth_token)
            return _orig(*a, **kw)
        _hf.hf_hub_download = _compat

        from pathlib import Path
        import speechbrain.pretrained as sb_pre
        model_dir = str(Path.home() / ".ltsia" / "models" / "spkrec-ecapa")
        t0 = time.monotonic()
        encoder = sb_pre.EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=model_dir,
            run_opts={"device": "cpu"},
        )
        encoder.eval()
        load_ms = (time.monotonic() - t0) * 1000
        _ok(f"Modello caricato in {load_ms:.0f}ms")
    except Exception as exc:
        _fail(f"Caricamento fallito: {exc}")
        return

    print(f"\n  {'Audio':>8}  {'Embedding':>12}")
    print(f"  {'─'*7}  {'─'*12}")
    for dur in [1.0, 2.0, 3.0]:
        audio = _make_speech_like(dur).unsqueeze(0) if False else None
        wav = torch.tensor(_make_speech_like(dur)).unsqueeze(0)
        t0 = time.monotonic()
        with torch.no_grad():
            emb = encoder.encode_batch(wav)
        emb_ms = (time.monotonic() - t0) * 1000
        print(f"  {dur:>7.1f}s  {emb_ms:>11.0f}ms")

    # Simula enroll + verify
    print("\n  Simulazione enroll (5 segmenti × 2s)…")
    segs = [torch.tensor(_make_speech_like(2.0, freq=440 + i * 10)).unsqueeze(0) for i in range(5)]
    embeddings = []
    t0 = time.monotonic()
    with torch.no_grad():
        for s in segs:
            embeddings.append(encoder.encode_batch(s).squeeze().numpy())
    enroll_ms = (time.monotonic() - t0) * 1000
    vp = np.mean(embeddings, axis=0)
    vp /= np.linalg.norm(vp) + 1e-9
    _ok(f"Enroll 5 segs completato in {enroll_ms:.0f}ms ({enroll_ms/5:.0f}ms/seg)")

    wav_test = torch.tensor(_make_speech_like(2.0, freq=440)).unsqueeze(0)
    t0 = time.monotonic()
    with torch.no_grad():
        emb_test = encoder.encode_batch(wav_test).squeeze().numpy()
    verify_ms = (time.monotonic() - t0) * 1000
    score = float(np.dot(emb_test, vp) / (np.linalg.norm(emb_test) * np.linalg.norm(vp) + 1e-9))
    _ok(f"Verify completato in {verify_ms:.0f}ms → score={score:.3f}")
    print(f"\n  Nota: su audio sintetico i score non sono significativi.")
    print(f"  Usa 'Daniela allena il riconoscimento vocale' per enroll reale.")


# ── Riepilogo ────────────────────────────────────────────────────────────────

def summary(deps: dict[str, bool], model: str) -> None:
    _header("Riepilogo RPi5")
    whisper_ok = deps.get("faster-whisper", False)
    torch_ok   = deps.get("torch", False)
    sb_ok      = deps.get("speechbrain", False)

    print(f"  STT (Whisper {model}):       {'OK' if whisper_ok else 'MANCANTE — pip install faster-whisper'}")
    print(f"  VAD (webrtcvad):            {'OK' if deps.get('webrtcvad') else 'MANCANTE — pip install webrtcvad'}")
    print(f"  Speaker recognition:        {'OK' if (torch_ok and sb_ok) else 'DISABILITATO (speaker_verify=false nel ltsia.ini)'}")

    if not whisper_ok or not deps.get("webrtcvad"):
        print("\n  Installa dipendenze base:")
        print("    pip install faster-whisper webrtcvad numpy scipy")
    if not torch_ok or not sb_ok:
        print("\n  Per speaker recognition (opzionale, ~500MB):")
        print("    pip install torch --index-url https://download.pytorch.org/whl/cpu")
        print("    pip install speechbrain==0.5.16 huggingface_hub")
        print("\n  Con speaker_verify=false nel ltsia.ini l'assistente funziona")
        print("  senza verifica vocale: risponde a chiunque dica 'Daniela'.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark voce per Raspberry Pi 5")
    parser.add_argument("--model",    default="tiny", choices=["tiny", "base", "small", "medium"])
    parser.add_argument("--stt-only", action="store_true", help="Salta speaker recognition")
    args = parser.parse_args()

    print(f"\nBenchmark voce — RPi5 (ARM64)")
    import platform
    print(f"Python {platform.python_version()} | {platform.machine()} | {platform.system()}")

    deps = check_deps()
    benchmark_stt(args.model)
    if not args.stt_only:
        benchmark_speaker()
    summary(deps, args.model)
    print()


if __name__ == "__main__":
    main()
