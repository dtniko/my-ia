"""SearchAgent — ricerca web multi-sorgente parallela con sintesi LLM."""
from __future__ import annotations
from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry
from .base_agent import BaseAgent

SYSTEM_PROMPT = """Sei un esperto ricercatore web. Usa web_search e web_fetch per trovare informazioni accurate.

PROCESSO:
1. Formulare query precise (varianti se necessario)
2. Cercare con web_search
3. Approfondire con web_fetch se utile
4. Sintetizzare i risultati in una risposta chiara

Massimo 6 tool call, poi sintetizza. Rispondi in italiano."""


class SearchAgent(BaseAgent):
    MAX_ITERATIONS = 8

    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        context_window: int = 131072,
    ):
        super().__init__(client, model, registry, context_window)

    def search(self, query: str) -> str:
        context = ContextManager(context_window=self.context_window)
        context.set_system_prompt(SYSTEM_PROMPT)
        context.add_user_message(f"Cerca informazioni su: {query}")
        tools = self.registry.get_planning_tool_schemas()
        return self._run_agent_loop(context, tools, max_iterations=8)
