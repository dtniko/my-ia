"""
ChatAgent — loop conversazionale principale.
Mantiene contesto persistente su più turn, streaming, compaction automatica.
"""
from __future__ import annotations
import platform
import sys
from typing import Callable, Optional

from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager
from src.tools.tool_registry import ToolRegistry
from src.memory.permanent_memory import PermanentMemory
from .base_agent import BaseAgent


SYSTEM_PROMPT_TEMPLATE = """Sei un agente software autonomo intelligente che aiuta a costruire, testare e migliorare progetti software.

## Identità
Il tuo nome predefinito è LTSIA, ma l'utente può averti assegnato un altro nome
o ruolo. Le istruzioni e memorie qui sotto hanno PRIORITÀ sul nome predefinito:
se trovi una memoria del tipo "ti chiami X" o "sei Y", adottala senza esitare.

Sistema operativo: {os_name}
Work directory: {work_dir}

## Capacità
- Pianifica progetti (usa plan_project)
- Crea file di codice (usa delegate_file_creation per ogni file)
- Testa progetti (usa run_tests)
- Cerca informazioni nel web
- Gestisce file, esegue comandi
- Ricorda istruzioni importanti cross-sessione

## Regola memoria
Quando l'utente dice "ricordati", "ricorda", "da ora", "da oggi", "memorizza",
"chiamami X", "ti chiami Y" → usa SEMPRE il tool `remember` per salvare
l'istruzione in memoria permanente. Dopo aver salvato, conferma brevemente.
Non rispondere "ok, ricordato" senza aver chiamato il tool.

## Regole PTC (Plan-Tool-Call)
- NON scrivere codice nei tuoi messaggi — usa i tool per creare file
- Pianifica PRIMA, poi crea i file uno per volta con delegate_file_creation
- Dopo aver creato tutti i file, usa run_tests per verificare

## Regola informazioni di sistema
- Per ottenere ora, data, variabili d'ambiente o qualsiasi info di sistema → usa SEMPRE execute_command (es: `date`, `echo $HOME`)
- NON rispondere "non ho accesso" se hai execute_command disponibile

## Regola curl / richieste HTTP
- Quando usi `curl` via execute_command, aggiungi SEMPRE `--max-time 30` per evitare che si appenda su server lenti o connessioni keep-alive
- Esempio: `curl --max-time 30 http://localhost:3000`

## Regole macOS
- Per APRIRE un'app (Spotify, Safari, Terminal, Finder, ecc.) → usa SEMPRE macos_open_app con target="NomeApp"
- NON usare read_file, execute_command o altri tool per aprire applicazioni
- Per controllare cosa sta girando → usa macos_list_apps
- Per automazioni UI → usa applescript

## Regola auto-estensibilità
- Se ti viene chiesto di creare un nuovo tool/modulo, usa create_module
- Il nuovo tool sarà disponibile IMMEDIATAMENTE dopo la creazione
- Esempio: "crea un tool per il meteo" → usa create_module con il codice Python del tool

{memory_section}

Rispondi sempre in italiano. Sii conciso e diretto.
"""


class ChatAgent(BaseAgent):
    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        registry: ToolRegistry,
        memory: PermanentMemory,
        work_dir: str,
        context_window: int = 131072,
        initial_context: str = "",
        scope_name: str = "session",
    ):
        super().__init__(client, model, registry, context_window)
        self.memory = memory
        self.work_dir = work_dir
        self._initial_context = initial_context
        self.context = ContextManager(context_window=context_window, compaction_threshold=0.75)
        self.scope_name = scope_name
        self._memory_orchestrator = None
        self._setup_context()

    def _setup_context(self):
        mem_section = self.memory.format_for_prompt()
        if self._initial_context:
            mem_section = self._initial_context + ("\n\n" + mem_section if mem_section else "")
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            os_name=platform.system(),
            work_dir=self.work_dir,
            memory_section=mem_section,
        )
        self.context.set_system_prompt(system_prompt)

        # Compact callback
        def compact_cb(messages):
            try:
                summary_msgs = [
                    {"role": "system", "content": "Riassumi brevemente la conversazione seguente in italiano, preservando dettagli tecnici importanti."},
                    {"role": "user", "content": str(messages)},
                ]
                r = self.client.chat(model=self.model, messages=summary_msgs)
                return r["message"].get("content", "")
            except Exception:
                return ""

        self.context.set_compact_callback(compact_cb)

    def set_memory_orchestrator(self, orchestrator) -> None:
        """Collega il MemoryOrchestratorAgent per enrichment pre-turn e ingest post-turn."""
        self._memory_orchestrator = orchestrator

    def chat(
        self,
        user_message: str,
        on_stream: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Invia un messaggio e ottieni risposta. Mantiene contesto."""

        # Pre-turn: arricchimento via memoria tiered + eventuale web fallback
        if self._memory_orchestrator:
            try:
                enriched = self._memory_orchestrator.enrich_request(user_message, scope=self.scope_name)
                if enriched.has_content():
                    preface = (
                        "Contesto recuperato automaticamente dalla memoria / web. "
                        "Usalo per rispondere se pertinente, altrimenti ignoralo.\n\n"
                        + enriched.text
                    )
                    self.context.add_user_message(preface)
            except Exception:
                pass

        self.context.add_user_message(user_message)
        tools = self.registry.get_chat_tool_schemas()
        response = self._run_agent_loop(
            context=self.context,
            tool_schemas=tools,
            on_stream=on_stream,
        )

        # Post-turn: ingest di drawer in short-term + estrazione fatti via ASMR reader
        if self._memory_orchestrator:
            try:
                written = self._memory_orchestrator.ingest_turn(
                    user_msg=user_message,
                    assistant_msg=response or "",
                    scope=self.scope_name,
                )
                # Se è stata aggiunta una direttiva permanente, rigenera system prompt
                # così il turno successivo la rispetta senza aspettare il restart.
                if written.get("permanent", 0) > 0:
                    self.update_memory()
            except Exception:
                pass

        return response

    def reset_context(self):
        """Resetta il context (mantiene system prompt)."""
        self.context.clear()

    def get_stats(self) -> dict:
        return self.context.get_stats()

    def update_memory(self):
        """Ricarica la memoria permanente e aggiorna il system prompt."""
        self._setup_context()

    def set_initial_context(self, ctx: str):
        """Aggiorna il contesto iniziale iniettato e ricarica il system prompt."""
        self._initial_context = ctx
        self._setup_context()
