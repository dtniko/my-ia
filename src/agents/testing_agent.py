"""TestingAgent — esegue test e auto-corregge errori (one-shot mode)."""
from __future__ import annotations
from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry
from .base_agent import BaseAgent

SYSTEM_PROMPT = """Sei un esperto QA engineer. Devi testare un progetto ed eventualmente correggere errori.

PROCESSO:
1. Esegui il test command fornito
2. Se fallisce: analizza l'errore, leggi i file rilevanti, scrivi una fix
3. Ripeti finché il test passa o esaurisci i tentativi
4. Produci un report finale JSON: {"success": true/false, "attempts": N, "summary": "..."}

Usa execute_command per eseguire test, read_file per analizzare, write_file per correggere."""


class TestingAgent(BaseAgent):
    MAX_ITERATIONS = 20

    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        max_retries: int = 5,
        context_window: int = 131072,
    ):
        super().__init__(client, model, registry, context_window)
        self.max_retries = max_retries

    def test(self, test_command: str, project_dir: str) -> dict:
        """Esegui test e auto-correggi. Ritorna {"success": bool, "attempts": int, "summary": str}."""
        context = ContextManager(context_window=self.context_window)
        context.set_system_prompt(SYSTEM_PROMPT)
        context.add_user_message(
            f"Testa il progetto in `{project_dir}`.\n"
            f"Comando test: `{test_command}`\n"
            f"Max tentativi: {self.max_retries}\n\n"
            "Esegui il test, correggi eventuali errori, poi rispondi con il JSON del report."
        )
        tools = self.registry.get_testing_tool_schemas()
        result = self._run_agent_loop(context, tools, max_iterations=self.MAX_ITERATIONS)

        # Prova a parsare JSON dal risultato
        import json, re
        m = re.search(r'\{[^{}]*"success"[^{}]*\}', result, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass

        success = "success" in result.lower() and "false" not in result.lower()
        return {"success": success, "attempts": 1, "summary": result[:500]}
