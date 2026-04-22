"""
StreamFilter — nasconde i blocchi <tool_call>...</tool_call> durante lo streaming.

In modalità PTC il modello emette i tool call come testo inline:

    <tool_call>
    {"name": "execute_command", "arguments": {"command": "date"}}
    </tool_call>

Senza filtro questo XML arriva dritto al terminale. Lo StreamFilter
riconosce i marker anche quando cadono a cavallo di chunk diversi e li
sostituisce con un indicatore compatto (es.  ⚙ execute_command), invocando
anche una callback con il nome del tool e gli argomenti (per /last).
"""
from __future__ import annotations
import json
import re
from typing import Callable, Optional

from src.ui.cli import CLI


_OPEN = "<tool_call>"
_CLOSE = "</tool_call>"


class StreamFilter:
    def __init__(
        self,
        downstream: Callable[[str], None],
        on_tool_call: Optional[Callable[[str, str], None]] = None,
        show_indicator: bool = True,
    ):
        self.downstream = downstream
        self.on_tool_call = on_tool_call
        self.show_indicator = show_indicator
        self._buf = ""
        self._in_call = False
        self._call_buf = ""

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._buf += chunk
        while True:
            if not self._in_call:
                idx = self._buf.find(_OPEN)
                if idx == -1:
                    # Potrebbe esserci un prefisso del tag in coda: trattieni gli
                    # ultimi len(_OPEN)-1 caratteri per evitare di stampare "<tool"
                    hold = len(_OPEN) - 1
                    if len(self._buf) > hold:
                        self.downstream(self._buf[:-hold])
                        self._buf = self._buf[-hold:]
                    return
                if idx > 0:
                    self.downstream(self._buf[:idx])
                self._buf = self._buf[idx + len(_OPEN):]
                self._in_call = True
                self._call_buf = ""
            else:
                idx = self._buf.find(_CLOSE)
                if idx == -1:
                    # Trattieni la coda per intercettare il tag anche se straddle
                    hold = len(_CLOSE) - 1
                    if len(self._buf) > hold:
                        self._call_buf += self._buf[:-hold]
                        self._buf = self._buf[-hold:]
                    return
                self._call_buf += self._buf[:idx]
                self._buf = self._buf[idx + len(_CLOSE):]
                self._in_call = False
                self._emit_indicator(self._call_buf.strip())
                self._call_buf = ""

    def flush(self) -> None:
        """Stampa il residuo non ancora emesso (coda del buffer senza tag)."""
        if not self._in_call and self._buf:
            self.downstream(self._buf)
        self._buf = ""

    def _emit_indicator(self, raw: str) -> None:
        name = _extract_tool_name(raw) or "tool"
        if self.on_tool_call:
            try:
                self.on_tool_call(name, raw)
            except Exception:
                pass
        if self.show_indicator:
            self.downstream("\n" + CLI.dim(f"  ⚙ {name}") + "\n")


_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')


def _extract_tool_name(raw: str) -> Optional[str]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            n = data.get("name")
            if isinstance(n, str):
                return n
    except Exception:
        pass
    m = _NAME_RE.search(raw)
    return m.group(1) if m else None
