"""
VADProcessor — wrapper leggero su webrtcvad per il rilevamento voce frame-per-frame.

Espone un singolo metodo is_speech_frame() usato dal pipeline streaming.
"""
from __future__ import annotations


class VADProcessor:
    """
    Wrappa webrtcvad.Vad per il rilevamento voce frame-per-frame.

    Formato atteso: frame PCM int16 a 16kHz, durata 30 ms (480 campioni = 960 byte).
    """

    SAMPLE_RATE    = 16000
    FRAME_DURATION = 30          # ms
    FRAME_SAMPLES  = SAMPLE_RATE * FRAME_DURATION // 1000  # 480
    FRAME_BYTES    = FRAME_SAMPLES * 2                      # 960  (int16 = 2 byte)

    def __init__(self, aggressiveness: int = 2):
        """
        aggressiveness: 0–3
          0 = poco selettivo (cattura anche voce lontana)
          2 = buon compromesso per uso domestico con TV in sottofondo
          3 = molto selettivo (solo voce vicina e forte)
        """
        if aggressiveness not in (0, 1, 2, 3):
            raise ValueError("aggressiveness deve essere 0–3")
        self._agg = aggressiveness
        self._vad = None

    def _load(self) -> None:
        if self._vad is not None:
            return
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self._agg)
        except ImportError as e:
            raise ImportError(
                "webrtcvad non installato — esegui: pip install webrtcvad"
            ) from e

    def is_speech_frame(self, frame_bytes: bytes) -> bool:
        """
        Verifica se un frame di 960 byte (480 campioni int16 @ 16 kHz) contiene voce.
        Restituisce False in caso di errore anziché sollevare eccezione.
        """
        self._load()
        if len(frame_bytes) != self.FRAME_BYTES:
            return False
        try:
            return bool(self._vad.is_speech(frame_bytes, self.SAMPLE_RATE))
        except Exception:
            return False
