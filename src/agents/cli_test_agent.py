"""CLITestAgent — testa script CLI (PHP, Python, Go, Bash)."""
from __future__ import annotations
from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry
from .base_agent import BaseAgent

SYSTEM_PROMPT = """Sei un esperto tester CLI. Testa script non-web (PHP, Python, Go, Bash, etc.).

PROCESSO:
1. Analizza il progetto: lista file, leggi codice principale
2. Esegui syntax check se applicabile
3. Esegui il test/script
4. Valida output
5. Produci report: {"success": true/false, "output": "...", "issues": [...]}

NON usare browser tool. Usa solo: execute_command, read_file, write_file, list_directory, glob_search, grep_search."""


class CLITestAgent(BaseAgent):
    MAX_ITERATIONS = 20

    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        context_window: int = 131072,
    ):
        super().__init__(client, model, registry, context_window)

    def test(self, project_dir: str, test_command: str = "") -> str:
        context = ContextManager(context_window=self.context_window)
        context.set_system_prompt(SYSTEM_PROMPT)
        prompt = f"Testa il progetto CLI in: {project_dir}"
        if test_command:
            prompt += f"\nComando test: {test_command}"
        context.add_user_message(prompt)
        tools = self.registry.get_testing_tool_schemas()
        return self._run_agent_loop(context, tools, max_iterations=self.MAX_ITERATIONS)
