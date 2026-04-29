"""
ToolRegistry — registro centralizzato di tutti i tool.
Supporta hot-reload runtime: nuovi tool Python scritti su disco vengono caricati
senza riavviare il processo.
"""
from __future__ import annotations
import importlib
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .base_tool import BaseTool


class ToolRegistry:
    def __init__(self, work_dir: str, config=None):
        self.work_dir = work_dir
        self.config = config
        self._tools: dict[str, BaseTool] = {}
        self._delegates: dict[str, Callable] = {}
        self._output_callback: Optional[Callable[[str], None]] = None
        self._execution_listener: Optional[Callable[[str, dict, str], None]] = None

        # Carica tool built-in
        self._register_builtin_tools()

    # ── Registrazione ──────────────────────────────────────────────────────────

    def register(self, tool: BaseTool):
        self._tools[tool.get_name()] = tool

    def _register_builtin_tools(self):
        from .filesystem.read_file import ReadFileTool
        from .filesystem.write_file import WriteFileTool
        from .filesystem.list_directory import ListDirectoryTool
        from .filesystem.create_directory import CreateDirectoryTool
        from .filesystem.delete_file import DeleteFileTool
        from .filesystem.move_file import MoveFileTool
        from .filesystem.glob_search import GlobSearchTool
        from .filesystem.grep_search import GrepSearchTool
        from .shell.execute_command import ExecuteCommandTool
        from .shell.smart_install import SmartInstallTool
        from .web.web_search import WebSearchTool
        from .web.web_fetch import WebFetchTool

        fs_tools = [
            ReadFileTool(self.work_dir),
            WriteFileTool(self.work_dir),
            ListDirectoryTool(self.work_dir),
            CreateDirectoryTool(self.work_dir),
            DeleteFileTool(self.work_dir),
            MoveFileTool(self.work_dir),
            GlobSearchTool(self.work_dir),
            GrepSearchTool(self.work_dir),
        ]
        for t in fs_tools:
            self.register(t)

        exec_tool = ExecuteCommandTool(self.work_dir, output_callback=self._get_output_cb())
        self.register(exec_tool)
        self.register(SmartInstallTool(self.work_dir))
        self.register(WebSearchTool())
        self.register(WebFetchTool())

        # Tool browser opzionali
        try:
            from .browser.start_dev_server import StartDevServerTool
            from .browser.stop_dev_server import StopDevServerTool
            from .browser.browser_test import BrowserTestTool
            self.register(StartDevServerTool(self.work_dir))
            self.register(StopDevServerTool())
            self.register(BrowserTestTool(self.work_dir))
        except Exception:
            pass

        # Tool macOS
        import sys as _sys
        if _sys.platform == "darwin":
            try:
                from .macos.applescript import MacOSAppleScriptTool
                from .macos.clipboard import MacOSClipboardTool
                from .macos.screenshot import MacOSScreenshotTool
                from .macos.open_app import MacOSOpenAppTool
                from .macos.list_apps import MacOSListAppsTool
                for t in [MacOSAppleScriptTool(), MacOSClipboardTool(),
                          MacOSScreenshotTool(self.work_dir), MacOSOpenAppTool(), MacOSListAppsTool()]:
                    self.register(t)
            except Exception:
                pass

        # Tool memoria base — inizializzati senza memorie, aggiornati via register_memory_tools()
        try:
            from .memory_tools.remember import RememberTool
            from .memory_tools.forget import ForgetTool
            from .memory_tools.list_memories import ListMemoriesTool
            from src.memory.core_facts import CoreFactsMemory
            _core = CoreFactsMemory()
            for t in [RememberTool(_core), ForgetTool(_core), ListMemoriesTool(_core)]:
                self.register(t)
        except Exception:
            pass

        # Tool Qdrant Viz (interfaccia web 3D per esplorare la memoria vettoriale)
        try:
            from .qdrant_viz.viz_tool import QdrantVizTool
            self.register(QdrantVizTool(self.config))  # qdrant_memory aggiunto dopo via register_qdrant_memory
        except Exception:
            pass

    def register_memory_tools(self, core_facts, qdrant_memory=None, medium_term=None) -> None:
        """Aggiorna i tool memoria con i riferimenti alle istanze di memoria."""
        try:
            from .memory_tools.remember import RememberTool
            from .memory_tools.forget import ForgetTool
            from .memory_tools.list_memories import ListMemoriesTool
            self.register(RememberTool(core_facts, qdrant_memory, medium_term))
            self.register(ForgetTool(core_facts, qdrant_memory))
            self.register(ListMemoriesTool(core_facts, qdrant_memory, medium_term))
        except Exception:
            pass

    def register_qdrant_memory(self, qdrant_memory) -> None:
        """Aggiorna QdrantVizTool con il client locale (necessario in qdrant_mode=local)."""
        try:
            from .qdrant_viz.viz_tool import QdrantVizTool
            tool = self._tools.get("qdrant_viz")
            if tool and isinstance(tool, QdrantVizTool):
                tool.qdrant_memory = qdrant_memory
        except Exception:
            pass

    def register_semantic_memory(self, semantic_memory) -> None:
        """Registra il tool search_memory (richiede SemanticMemory inizializzata)."""
        try:
            from .memory_tools.search_memory import SearchMemoryTool
            self.register(SearchMemoryTool(semantic_memory))
        except Exception:
            pass

    def register_job_manager(self, job_manager) -> None:
        """Registra i tool schedule_job / cancel_job / list_jobs."""
        try:
            from .jobs_tools.schedule_job import ScheduleJobTool
            from .jobs_tools.cancel_job   import CancelJobTool
            from .jobs_tools.list_jobs    import ListJobsTool
            for t in [ScheduleJobTool(job_manager), CancelJobTool(job_manager), ListJobsTool(job_manager)]:
                self.register(t)
        except Exception:
            pass

    def _get_output_cb(self):
        def cb(text):
            if self._output_callback:
                self._output_callback(text)
        return cb

    # ── Delegates (pseudo-tool) ────────────────────────────────────────────────

    def set_delegate(self, name: str, callback: Callable):
        """Registra una pseudo-tool delegata (es. plan_project, delegate_file_creation)."""
        self._delegates[name] = callback

    def set_planning_delegate(self, cb: Callable):
        self.set_delegate("plan_project", cb)

    def set_file_creation_delegate(self, cb: Callable):
        self.set_delegate("delegate_file_creation", cb)

    def set_testing_delegate(self, cb: Callable):
        self.set_delegate("run_tests", cb)

    def set_search_delegate(self, cb: Callable):
        self.set_delegate("web_search_advanced", cb)

    def set_command_output_callback(self, cb: Callable[[str], None]):
        self._output_callback = cb

    def set_execution_listener(self, cb: Optional[Callable[[str, dict, str], None]]):
        """Callback invocata dopo ogni tool.execute() con (name, args, result)."""
        self._execution_listener = cb

    # ── Esecuzione ─────────────────────────────────────────────────────────────

    def execute(self, tool_name: str, args: dict) -> str:
        # Prima controlla i delegate
        if tool_name in self._delegates:
            result = self._delegates[tool_name](args)
        else:
            tool = self._tools.get(tool_name)
            if not tool:
                result = f"ERROR: tool '{tool_name}' non trovato. Tool disponibili: {', '.join(self._tools.keys())}"
            else:
                try:
                    result = tool.execute(args)
                except Exception as e:
                    result = f"ERROR in {tool_name}: {e}"
        if self._execution_listener:
            try:
                self._execution_listener(tool_name, args, str(result))
            except Exception:
                pass
        return result

    # ── Schema ─────────────────────────────────────────────────────────────────

    def get_chat_tool_schemas(self) -> list[dict]:
        """Tutti i tool + pseudo-tool per ChatAgent."""
        schemas = [t.to_schema() for t in self._tools.values()]
        schemas += self._get_pseudo_tool_schemas()
        return schemas

    def get_planning_tool_schemas(self) -> list[dict]:
        """Solo tool di ricerca read-only per ProjectManagerAgent."""
        allowed = {"web_search", "web_fetch", "read_file", "list_directory", "glob_search", "grep_search"}
        return [t.to_schema() for name, t in self._tools.items() if name in allowed]

    def get_testing_tool_schemas(self) -> list[dict]:
        """Tool CLI-focused per TestingAgent/CLITestAgent."""
        allowed = {"execute_command", "read_file", "write_file", "list_directory",
                   "glob_search", "grep_search", "install_packages"}
        return [t.to_schema() for name, t in self._tools.items() if name in allowed]

    def _get_pseudo_tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "plan_project",
                    "description": "Pianifica architettura e struttura del progetto. Restituisce un piano dettagliato Markdown.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "description": "Descrizione del progetto da pianificare"},
                        },
                        "required": ["task"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_file_creation",
                    "description": "Crea un singolo file con il contenuto specificato. Chiama per ogni file del progetto.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Percorso del file relativo al work_dir"},
                            "description": {"type": "string", "description": "Descrizione del file e cosa deve fare"},
                            "context": {"type": "string", "description": "Contesto: piano, altri file già creati"},
                        },
                        "required": ["path", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Testa il progetto creato. Rileva automaticamente il tipo (web/CLI) e avvia i test.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_dir": {"type": "string", "description": "Directory del progetto"},
                            "test_command": {"type": "string", "description": "Comando di test opzionale"},
                        },
                        "required": ["project_dir"],
                    },
                },
            },
        ]

    # ── Hot-reload runtime ──────────────────────────────────────────────────────

    def load_module_from_file(self, file_path: str) -> list[str]:
        """
        Carica un modulo Python da file e registra tutti i tool trovati.
        Ritorna la lista di nomi tool caricati.
        Questo è il cuore dell'auto-estensibilità: un LLM crea un file Python
        con uno o più BaseTool, e questo metodo li carica IMMEDIATAMENTE senza restart.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File non trovato: {file_path}")
        if not path.suffix == ".py":
            raise ValueError(f"Il file deve essere .py: {file_path}")

        module_name = f"ltsia_dynamic.{path.stem}_{id(path)}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        loaded = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (inspect.isclass(attr)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                    and not inspect.isabstract(attr)):
                try:
                    instance = attr()
                    self.register(instance)
                    loaded.append(instance.get_name())
                except Exception:
                    # Prova con work_dir
                    try:
                        instance = attr(self.work_dir)
                        self.register(instance)
                        loaded.append(instance.get_name())
                    except Exception as e:
                        pass

        return loaded

    def scan_and_load_dynamic_tools(self, directory: str) -> list[str]:
        """
        Scansiona una directory per file Python con tool e li carica tutti.
        Utile per caricare tool creati a runtime.
        """
        loaded_all = []
        for py_file in Path(directory).glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                loaded = self.load_module_from_file(str(py_file))
                loaded_all.extend(loaded)
            except Exception:
                pass
        return loaded_all

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        return name in self._tools or name in self._delegates
