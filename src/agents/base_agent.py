"""Base per tutti gli agenti."""
from __future__ import annotations
import json
from typing import Any, Callable, Optional

from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry


class BaseAgent:
    MAX_ITERATIONS = 30

    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        context_window: int = 131072,
    ):
        self.client = client
        self.model = model
        self.registry = registry
        self.context_window = context_window

    def _run_agent_loop(
        self,
        context: ContextManager,
        tool_schemas: list[dict],
        on_stream: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        max_iterations: Optional[int] = None,
    ) -> str:
        """Loop agente PTC: chiama LLM → esegui tool → ripeti."""
        max_iter = max_iterations or self.MAX_ITERATIONS
        last_content = ""

        for iteration in range(max_iter):
            # Compaction se necessario
            if context.needs_compaction():
                context.compact()

            try:
                result = self.client.chat(
                    model=self.model,
                    messages=context.get_messages(),
                    tools=tool_schemas if tool_schemas else None,
                    on_stream=on_stream,
                )
            except Exception as e:
                return f"ERROR: LLM call fallita: {e}"

            message = result.get("message", {})
            usage = result.get("usage", {})
            context.update_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

            content = message.get("content", "") or ""
            tool_calls = message.get("tool_calls", []) or []
            last_content = content

            context.add_assistant_message(content, tool_calls if tool_calls else None)

            if not tool_calls:
                # LLM ha terminato
                return content

            # Esegui tool calls
            tool_results = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                tc_id = tc.get("id", "tc_0")

                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}

                if on_tool_start:
                    try:
                        on_tool_start(tool_name, args)
                    except Exception:
                        pass

                tool_result = self.registry.execute(tool_name, args)
                tool_results.append({
                    "tool_call_id": tc_id,
                    "content": str(tool_result),
                })

            context.add_tool_results(tool_results)

        return last_content or "ERROR: raggiunto limite iterazioni"
