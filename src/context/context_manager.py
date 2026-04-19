"""
ContextManager — gestisce history messaggi, stima token, compaction.
"""
from __future__ import annotations
import json
from typing import Any, Callable, Optional


class ContextManager:
    def __init__(
        self,
        context_window: int = 32768,
        compaction_threshold: float = 0.75,
    ):
        self.context_window = context_window
        self.compaction_threshold = compaction_threshold
        self._messages: list[dict] = []
        self._system_prompt: str = ""
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._compaction_count: int = 0
        self._compact_callback: Optional[Callable[[list[dict]], str]] = None

    def set_compact_callback(self, cb: Callable[[list[dict]], str]):
        """Callback che riceve i messaggi e ritorna un summary string."""
        self._compact_callback = cb

    def set_system_prompt(self, prompt: str):
        self._system_prompt = prompt

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def add_user_message(self, content: str):
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str, tool_calls: Optional[list] = None):
        msg: dict = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)

    def add_tool_results(self, results: list[dict]):
        """Aggiungi risultati tool. results = [{"tool_call_id": ..., "content": ...}]"""
        for r in results:
            self._messages.append({
                "role": "tool",
                "tool_call_id": r.get("tool_call_id", "0"),
                "content": str(r.get("content", "")),
            })

    def get_messages(self) -> list[dict]:
        """Ritorna i messaggi con system prompt in testa."""
        msgs = []
        if self._system_prompt:
            msgs.append({"role": "system", "content": self._system_prompt})
        msgs.extend(self._messages)
        return msgs

    def update_usage(self, prompt_tokens: int, completion_tokens: int):
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens

    def estimate_tokens(self) -> int:
        """Stima approssimativa: chars / 4."""
        total = len(self._system_prompt)
        for m in self._messages:
            content = m.get("content") or ""
            total += len(str(content))
            if m.get("tool_calls"):
                total += len(json.dumps(m["tool_calls"]))
        return total // 4

    def needs_compaction(self) -> bool:
        return self.estimate_tokens() >= int(self.context_window * self.compaction_threshold)

    def compact(self) -> bool:
        """Compatta il context. Ritorna True se compaction avvenuta."""
        if not self._compact_callback or not self._messages:
            return False
        summary = self._compact_callback(self._messages)
        if summary:
            self._messages = [
                {"role": "user", "content": f"[CONTEXT COMPACTED]\n\n{summary}"},
            ]
            self._compaction_count += 1
            return True
        return False

    def export_memory(self, max_messages: int = 40) -> list[dict]:
        """Esporta ultimi N messaggi non-system per persistenza."""
        return self._messages[-max_messages:]

    def import_memory(self, messages: list[dict]):
        """Importa messaggi precedentemente salvati."""
        self._messages = messages

    def get_stats(self) -> dict:
        return {
            "messages": len(self._messages),
            "estimated_tokens": self.estimate_tokens(),
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "compactions": self._compaction_count,
        }

    def clear(self):
        self._messages = []
