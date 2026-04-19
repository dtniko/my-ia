"""BaseTool — classe astratta base per tutti i tool."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    MAX_TRUNCATE = 32000

    @abstractmethod
    def get_name(self) -> str: ...

    @abstractmethod
    def get_description(self) -> str: ...

    @abstractmethod
    def get_parameters(self) -> dict: ...

    @abstractmethod
    def execute(self, args: dict) -> str: ...

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.get_name(),
                "description": self.get_description(),
                "parameters": self.get_parameters(),
            },
        }

    def truncate(self, text: str, max_chars: int = 0) -> str:
        limit = max_chars or self.MAX_TRUNCATE
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n[... troncato a {limit} caratteri]"
