"""Client Ollama /api/chat — usato per modelli coder (thinking)."""
from __future__ import annotations
import json
import re
import requests
from typing import Any, Callable, Optional

from .llm_client import LlmClientInterface


class OllamaClient(LlmClientInterface):
    def __init__(self, base_url: str, timeout: int = 1800):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        on_stream: Optional[Callable[[str], None]] = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": on_stream is not None,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/api/chat"
        try:
            if on_stream:
                return self._stream(url, payload, on_stream)
            else:
                return self._blocking(url, payload)
        except Exception as e:
            # fallback a non-streaming se streaming fallisce
            if on_stream:
                payload["stream"] = False
                return self._blocking(url, payload)
            raise

    def _blocking(self, url: str, payload: dict) -> dict:
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        message = data.get("message", {})
        # Prova a parsare XML tool calls se presenti
        message = self._normalize_message(message)
        return {
            "message": message,
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
        }

    def _stream(self, url: str, payload: dict, on_stream: Callable[[str], None]) -> dict:
        full_content = ""
        tool_calls: list[dict] = []
        usage: dict = {}

        with requests.post(url, json=payload, stream=True, timeout=self.timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = chunk.get("message", {})
                content = msg.get("content", "")
                if content:
                    full_content += content
                    on_stream(content)

                # tool_calls nello stream
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])

                if chunk.get("done"):
                    usage = {
                        "prompt_tokens": chunk.get("prompt_eval_count", 0),
                        "completion_tokens": chunk.get("eval_count", 0),
                    }

        message = {"role": "assistant", "content": full_content}
        if tool_calls:
            message["tool_calls"] = tool_calls

        message = self._normalize_message(message)
        return {"message": message, "usage": usage}

    def _normalize_message(self, message: dict) -> dict:
        """Parsa XML tool calls emesse da qwen3-coder nel campo content."""
        content = message.get("content", "") or ""
        if not content:
            return message

        xml_tools = self._parse_xml_tool_calls(content)
        if not xml_tools:
            return message

        # Trovate XML tool calls — aggiungi/merge
        existing = message.get("tool_calls", []) or []
        # Converti XML calls in formato OpenAI
        for i, xt in enumerate(xml_tools):
            existing.append({
                "id": f"xml_{i}",
                "type": "function",
                "function": {
                    "name": xt["name"],
                    "arguments": json.dumps(xt["args"]),
                },
            })

        clean_content = self._strip_xml_tool_calls(content)
        return {**message, "content": clean_content, "tool_calls": existing}

    def _parse_xml_tool_calls(self, text: str) -> list[dict]:
        """Parsa <function=name><parameter=key>value</parameter></function>"""
        pattern = r'<function=(\w+)>(.*?)</function>'
        param_pattern = r'<parameter=(\w+)>(.*?)</parameter>'
        results = []
        for m in re.finditer(pattern, text, re.DOTALL):
            name = m.group(1)
            body = m.group(2)
            args = {}
            for pm in re.finditer(param_pattern, body, re.DOTALL):
                args[pm.group(1)] = pm.group(2).strip()
            results.append({"name": name, "args": args})
        return results

    def _strip_xml_tool_calls(self, text: str) -> str:
        return re.sub(r'<function=\w+>.*?</function>', '', text, flags=re.DOTALL).strip()
