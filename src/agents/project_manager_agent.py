"""ProjectManagerAgent — ricerca + pianificazione, stateless."""
from __future__ import annotations
from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry
from .base_agent import BaseAgent

SYSTEM_PROMPT = """Sei un esperto architetto software. Il tuo compito è pianificare la struttura di un progetto.

REGOLE:
- Ricerca solo se necessario (usa web_search, web_fetch)
- NON creare file — solo pianificare
- Massimo 8 chiamate a tool, poi scrivi il piano
- Il piano deve essere Markdown completo con: struttura directory, file da creare, dipendenze, istruzioni build/run

Rispondi in italiano."""


class ProjectManagerAgent(BaseAgent):
    MAX_ITERATIONS = 10

    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        context_window: int = 131072,
    ):
        super().__init__(client, model, registry, context_window)

    def plan(self, task: str) -> str:
        """Genera un piano Markdown per il progetto dato."""
        context = ContextManager(context_window=self.context_window)
        context.set_system_prompt(SYSTEM_PROMPT)
        context.add_user_message(
            f"Pianifica questo progetto:\n\n{task}\n\n"
            "Produci un piano Markdown completo con struttura directory, file da creare, dipendenze e istruzioni."
        )
        tools = self.registry.get_planning_tool_schemas()
        return self._run_agent_loop(context, tools, max_iterations=10)
