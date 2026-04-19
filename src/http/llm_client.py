"""Interface comune per tutti i client LLM."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class LlmClientInterface(ABC):
    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        on_stream: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        Invia messaggi al modello.
        Ritorna: {"message": {"role": "assistant", "content": ..., "tool_calls": [...]}, "usage": {...}}
        """
        ...

    @abstractmethod
    def ping(self) -> bool:
        """Verifica connettività. True = ok."""
        ...
