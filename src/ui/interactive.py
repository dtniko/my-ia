"""REPL interattivo con readline."""
from __future__ import annotations
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.ui.cli import CLI
from src.ui.stream_filter import StreamFilter

if TYPE_CHECKING:
    from src.agents.chat_agent import ChatAgent
    from src.tools.tool_registry import ToolRegistry
    from src.jobs.job_manager import JobManager

HELP_TEXT = """
Comandi disponibili:
  /help         — questo messaggio
  /status       — statistiche context
  /tools        — elenca tool disponibili
  /modules      — elenca moduli dinamici caricati
  /jobs         — elenca background job
  /workdir      — mostra work directory
  /new          — resetta context (nuova sessione)
  /compact      — forza compattazione context
  /memories     — elenca memorie permanenti
  /last         — mostra dettagli dei tool call dell'ultimo turno
  /clear        — pulisci schermo
  /exit /quit   — esci

Auto-estensibilità:
  Puoi chiedere all'IA di creare nuovi tool con frasi come:
  "crea un tool per recuperare dati meteo"
  Il tool verrà creato e caricato IMMEDIATAMENTE senza riavvio.
"""

HISTORY_FILE = str(Path.home() / ".ltsia_history")


class Interactive:
    def __init__(
        self,
        agent: "ChatAgent",
        registry: "ToolRegistry",
        work_dir: str,
        voice_status: str = "",
        job_manager: Optional["JobManager"] = None,
    ):
        self.agent        = agent
        self.registry     = registry
        self.work_dir     = work_dir
        self.voice_status = voice_status
        self.job_manager  = job_manager
        # Stato notifiche background
        self._in_prompt   = False          # True solo durante input()
        self._notif_buffer: list = []      # notifiche accumulate fuori dal prompt
        self._notif_lock  = threading.Lock()
        # Tool call del turno corrente (catturati dallo StreamFilter + listener registry)
        self._turn_tool_calls: list[dict] = []
        self._last_tool_calls: list[dict] = []
        # Cattura silenziosa output comandi (disponibile via /last)
        self._turn_command_output: list[str] = []
        self.registry.set_command_output_callback(self._capture_command_output)
        self.registry.set_execution_listener(self._on_tool_executed)
        self._setup_readline()

    def _capture_command_output(self, line: str) -> None:
        self._turn_command_output.append(line)

    def _on_tool_executed(self, name: str, args: dict, result: str) -> None:
        """Aggiorna l'ultimo tool call senza risultato con questo risultato."""
        for tc in reversed(self._turn_tool_calls):
            if tc["name"] == name and tc.get("result") is None:
                tc["args_parsed"] = args if isinstance(args, dict) else {}
                tc["result"] = result
                return
        self._turn_tool_calls.append({
            "name": name,
            "args_raw": "",
            "args_parsed": args if isinstance(args, dict) else {},
            "result": result,
        })

    def _setup_readline(self):
        try:
            import readline
            if os.path.exists(HISTORY_FILE):
                readline.read_history_file(HISTORY_FILE)
            readline.set_history_length(500)
        except (ImportError, FileNotFoundError, OSError):
            pass

    def _save_history(self):
        try:
            import readline
            readline.write_history_file(HISTORY_FILE)
        except Exception:
            pass

    # ── Notifiche background ──────────────────────────────────────────────────

    def _start_notification_thread(self) -> None:
        """Avvia thread daemon che raccoglie output job e li mostra senza interrompere il REPL."""
        if not self.job_manager:
            return

        def _poll() -> None:
            while True:
                time.sleep(5)
                try:
                    outputs = self.job_manager.collect_pending_outputs()
                except Exception:
                    outputs = []
                for out in outputs:
                    if self._in_prompt:
                        self._inject_at_prompt(out)
                    else:
                        with self._notif_lock:
                            self._notif_buffer.append(out)

        t = threading.Thread(target=_poll, daemon=True, name="ltsia-notif")
        t.start()

    def _inject_at_prompt(self, out: dict) -> None:
        """Stampa una notifica sopra la readline prompt senza cancellare il testo in input."""
        desc    = out.get("description", out.get("type", "job"))
        content = out.get("content", "")
        header  = f"{CLI.bold(CLI.yellow('◆ Job:'))} {desc}"
        try:
            import readline
            saved      = readline.get_line_buffer()
            prompt_str = CLI.bold(CLI.cyan("you")) + " > "
            # Vai a inizio riga, cancella, stampa notifica, ridisegna prompt
            sys.stdout.write("\r\033[K")
            sys.stdout.write(header + "\n")
            if content:
                sys.stdout.write(CLI.dim(content) + "\n")
            sys.stdout.write("\n")
            sys.stdout.write(prompt_str + saved)
            sys.stdout.flush()
        except Exception:
            print(f"\n{header}")
            if content:
                print(CLI.dim(content))
            print()

    def _print_last_tool_calls(self) -> None:
        """Mostra nome, argomenti e risultato dei tool call dell'ultimo turno."""
        calls = self._last_tool_calls
        if not calls:
            print(CLI.dim("\n  Nessun tool call nell'ultimo turno.\n"))
            return
        print()
        for i, tc in enumerate(calls, 1):
            print(f"  {CLI.bold(f'[{i}]')} {CLI.cyan(tc['name'])}")
            args = tc.get("args_parsed") or {}
            raw = tc.get("args_raw") or ""
            if args:
                import json as _json
                print(CLI.dim("      args: " + _json.dumps(args, ensure_ascii=False)))
            elif raw:
                print(CLI.dim("      args: " + raw[:200]))
            result = tc.get("result")
            if result:
                snippet = result if len(result) <= 600 else result[:600] + f"\n      [... {len(result) - 600} char in più]"
                indented = "\n".join("      " + line for line in snippet.splitlines())
                print(CLI.dim(indented))
            else:
                print(CLI.dim("      (risultato non ancora disponibile)"))
        if self._turn_command_output:
            print(CLI.dim(f"\n  ── output comandi ({len(self._turn_command_output)} righe) ──"))
            for line in self._turn_command_output[-30:]:
                print(CLI.dim("    " + line.rstrip()))
        print()

    def _show_pending_job_outputs(self) -> None:
        """Mostra output accumulati nel buffer (chiamato prima di ogni prompt)."""
        with self._notif_lock:
            pending = list(self._notif_buffer)
            self._notif_buffer.clear()
        for out in pending:
            desc    = out.get("description", out.get("type", "job"))
            content = out.get("content", "")
            print(f"\n{CLI.bold(CLI.yellow('◆ Job:'))} {desc}")
            if content:
                print(CLI.dim(content))
            print()

    def run(self):
        CLI.banner()
        CLI.info(f"Work dir: {self.work_dir}")
        if self.voice_status:
            CLI.info(f"Voice:    {self.voice_status}")
            CLI.info("          Apri il voice tool React: cd voice && npm run dev")
        CLI.info("Digita /help per i comandi. Ctrl+C per uscire.")
        print()

        self._start_notification_thread()

        while True:
            # Mostra output accumulati nel buffer prima del prompt
            self._show_pending_job_outputs()

            self._in_prompt = True
            try:
                user_input = input(CLI.bold(CLI.cyan("you")) + " > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                CLI.info("Arrivederci!")
                self._save_history()
                break
            finally:
                self._in_prompt = False

            if not user_input:
                continue

            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    continue
                else:
                    break

            # Messaggio all'agente
            print(f"\n{CLI.bold(CLI.magenta('ltsia'))} > ", end="", flush=True)
            self._turn_tool_calls = []
            self._turn_command_output = []

            def _on_stream_tool_call(name: str, raw: str) -> None:
                self._turn_tool_calls.append({
                    "name": name,
                    "args_raw": raw,
                    "args_parsed": None,
                    "result": None,
                })

            stream_filter = StreamFilter(
                downstream=lambda chunk: print(chunk, end="", flush=True),
                on_tool_call=_on_stream_tool_call,
            )

            try:
                self.agent.chat(user_input, on_stream=stream_filter.feed)
                stream_filter.flush()
                print("\n")
            except KeyboardInterrupt:
                stream_filter.flush()
                print("\n[interrotto]")
            except Exception as e:
                stream_filter.flush()
                CLI.error(f"Errore: {e}")
                print()

            self._last_tool_calls = list(self._turn_tool_calls)

    def _handle_command(self, cmd: str) -> bool:
        """Ritorna False se si deve uscire."""
        cmd_lower = cmd.lower().strip()

        if cmd_lower in ("/exit", "/quit"):
            CLI.info("Arrivederci!")
            self._save_history()
            return False

        elif cmd_lower == "/help":
            print(HELP_TEXT)

        elif cmd_lower == "/status":
            stats = self.agent.get_stats()
            print(f"\n  Messaggi:       {stats['messages']}")
            print(f"  Token stimati:  {stats['estimated_tokens']:,}")
            print(f"  Token totali:   {stats['total_prompt_tokens'] + stats['total_completion_tokens']:,}")
            print(f"  Compaction:     {stats['compactions']}")
            print()

        elif cmd_lower == "/tools":
            tools = sorted(self.registry.list_tools())
            print(f"\n  {len(tools)} tool disponibili:")
            for t in tools:
                print(f"    • {t}")
            print()

        elif cmd_lower == "/modules":
            result = self.registry.execute("list_modules", {})
            print(result)

        elif cmd_lower == "/jobs":
            if self.job_manager:
                result = self.registry.execute("list_jobs", {})
                print(result if result else "Nessun job.")
            else:
                CLI.warning("JobManager non disponibile.")

        elif cmd_lower == "/workdir":
            CLI.info(f"Work dir: {self.work_dir}")

        elif cmd_lower == "/new":
            self.agent.reset_context()
            CLI.success("Context resettato")

        elif cmd_lower == "/compact":
            self.agent.context.compact()
            CLI.success("Context compattato")

        elif cmd_lower == "/memories":
            result = self.registry.execute("list_memories", {})
            print(result)

        elif cmd_lower == "/last":
            self._print_last_tool_calls()

        elif cmd_lower == "/clear":
            os.system("cls" if sys.platform == "win32" else "clear")

        else:
            CLI.warning(f"Comando sconosciuto: {cmd_lower}. Usa /help")

        return True
