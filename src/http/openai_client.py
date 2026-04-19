"""Client OpenAI-compatible (vLLM) /v1/chat/completions — usato per ChatAgent ed ExecutionAgent."""
from __future__ import annotations
import json
import requests
from typing import Any, Callable, Optional

from .llm_client import LlmClientInterface


class OpenAIClient(LlmClientInterface):
    def __init__(self, base_url: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=5)
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
            "temperature": 0.7,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.base_url}/v1/chat/completions"
        if on_stream:
            return self._stream(url, payload, on_stream)
        else:
            return self._blocking(url, payload)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> str:
        """Single-turn completion, ritorna stringa."""
        result = self.chat(
            model="",  # verrà ignorato — caller passa model via chat()
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return result["message"].get("content", "")

    def complete_streaming(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        on_chunk: Callable[[str], None],
        temperature: float = 0.7,
    ) -> str:
        """Single-turn streaming completion."""
        result = self.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            on_stream=on_chunk,
        )
        return result["message"].get("content", "")

    def _blocking(self, url: str, payload: dict) -> dict:
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage", {})
        # Normalizza tool_calls
        if message.get("tool_calls"):
            message["tool_calls"] = self._normalize_tool_calls(message["tool_calls"])
        return {
            "message": message,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        }

    def _stream(self, url: str, payload: dict, on_stream: Callable[[str], None]) -> dict:
        full_content = ""
        tool_calls_raw: dict[int, dict] = {}  # index → partial tool call
        usage: dict = {}

        with requests.post(url, json=payload, stream=True, timeout=self.timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    text = text[6:]
                if text == "[DONE]":
                    break
                try:
                    chunk = json.loads(text)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                if content:
                    full_content += content
                    on_stream(content)

                # Accumula tool_calls delta
                for tc in delta.get("tool_calls", []):
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_raw:
                        tool_calls_raw[idx] = {
                            "id": tc.get("id", f"tc_{idx}"),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        tool_calls_raw[idx]["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        tool_calls_raw[idx]["function"]["arguments"] += fn["arguments"]

                if chunk.get("usage"):
                    usage = {
                        "prompt_tokens": chunk["usage"].get("prompt_tokens", 0),
                        "completion_tokens": chunk["usage"].get("completion_tokens", 0),
                    }

        message: dict = {"role": "assistant", "content": full_content}
        if tool_calls_raw:
            tcs = [tool_calls_raw[i] for i in sorted(tool_calls_raw)]
            message["tool_calls"] = self._normalize_tool_calls(tcs)

        return {"message": message, "usage": usage}

    def _normalize_tool_calls(self, tool_calls: list) -> list:
        """Assicura che arguments sia stringa JSON."""
        result = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, dict):
                args = json.dumps(args)
            result.append({
                "id": tc.get("id", "tc_0"),
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": args},
            })
        return result
