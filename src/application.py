"""
Application — orchestratore principale LTSIA-py.
Inizializza tutto, wira delegate, gestisce modalità one-shot e interattiva.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

from src.config import Config
from src.http.ollama_client import OllamaClient
from src.http.openai_client import OpenAIClient
from src.http.ptc_adapter import PTCAdapter
from src.tools.tool_registry import ToolRegistry
from src.tools.create_module import CreateModuleTool, ReloadModuleTool, ListModulesTool
from src.memory.permanent_memory import PermanentMemory
from src.memory.short_term import ShortTermMemory
from src.memory.medium_term import MediumTermMemory
from src.memory.qdrant_memory import QdrantMemory
from src.agents.chat_agent import ChatAgent
from src.agents.context_agent import ContextAgent
from src.agents.memory_orchestrator_agent import MemoryOrchestratorAgent
from src.agents.memory_reader_agent import MemoryReaderAgent
from src.agents.memory_searcher_agent import MemorySearcherAgent
from src.agents.project_manager_agent import ProjectManagerAgent
from src.agents.execution_agent import ExecutionAgent
from src.agents.testing_agent import TestingAgent
from src.agents.cli_test_agent import CLITestAgent
from src.agents.search_agent import SearchAgent
from src.logger.session_logger import SessionLogger
from src.ui.cli import CLI


class Application:
    def __init__(self, config: Config):
        self.config = config
        self._ensure_dirs()

        CLI.step("Inizializzazione client LLM...")
        self.ollama = OllamaClient(config.thinking_base_url, timeout=config.ollama_timeout)
        self.openai = PTCAdapter(OpenAIClient(config.exec_base_url))

        CLI.step("Inizializzazione memoria...")
        self.memory = PermanentMemory()
        self.semantic_memory = None          # legacy SQLite (fallback)
        self.qdrant_memory = None            # long-term Qdrant
        self.short_term = None               # brevissimo termine (RAM)
        self.medium_term = None              # medio termine (MemPalace SQLite)
        self._init_semantic_memory()
        self._init_qdrant_memory()
        self._init_tiered_memory()

        CLI.step("Inizializzazione tool registry...")
        self.registry = ToolRegistry(config.work_dir, config)

        # Directory per moduli dinamici
        self.extensions_dir = str(Path.home() / ".ltsia" / "extensions")

        # Carica moduli dinamici già esistenti
        os.makedirs(self.extensions_dir, exist_ok=True)
        dynamic_loaded = self.registry.scan_and_load_dynamic_tools(self.extensions_dir)
        if dynamic_loaded:
            CLI.info(f"Moduli dinamici caricati: {', '.join(dynamic_loaded)}")

        # Registra tool auto-estensibilità
        self.registry.register(CreateModuleTool(self.registry, self.extensions_dir))
        self.registry.register(ReloadModuleTool(self.registry, self.extensions_dir))
        self.registry.register(ListModulesTool(self.registry, self.extensions_dir))

        # Registra tool memoria semantica: preferenza Qdrant, fallback SemanticMemory legacy
        active_semantic = self.qdrant_memory if (self.qdrant_memory and self.qdrant_memory.is_ready()) else self.semantic_memory
        if active_semantic:
            self.registry.register_semantic_memory(active_semantic)

        # Job manager
        self.job_manager = self._init_job_manager()
        if self.job_manager:
            self.registry.register_job_manager(self.job_manager)

        CLI.step("Inizializzazione ContextAgent...")
        self.context_agent = ContextAgent(
            permanent_memory=self.memory,
            semantic_memory=self.semantic_memory,
            warn_tokens=config.context_initial_warn_tokens,
            max_tokens=config.context_initial_max_tokens,
        )

        CLI.step("Inizializzazione agenti...")
        self._init_agents()
        self._init_memory_orchestrator()
        self._wire_delegates()
        self._wire_memory_orchestrator()

    def _ensure_dirs(self):
        for d in ["logs", "sessions", "errors", "extensions", "jobs"]:
            (Path.home() / ".ltsia" / d).mkdir(parents=True, exist_ok=True)
        Path(self.config.work_dir).mkdir(parents=True, exist_ok=True)

    def _init_qdrant_memory(self):
        """Inizializza la memoria a lungo termine su Qdrant, se raggiungibile."""
        cfg = self.config
        if not cfg.embedding_host:
            return
        try:
            from src.memory.embedding_client import EmbeddingClient
            embedder = EmbeddingClient(
                host=cfg.embedding_host,
                port=cfg.embedding_port,
                model=cfg.embedding_model,
                api_type=getattr(cfg, "embedding_api", "ollama"),
            )
            qm = QdrantMemory(
                host=cfg.qdrant_host,
                port=cfg.qdrant_port,
                collection=cfg.qdrant_collection,
                vector_size=cfg.qdrant_vector_size,
                embedder=embedder,
            )
            if qm.is_ready():
                self.qdrant_memory = qm
                CLI.success(
                    f"Qdrant memoria lungo termine: {cfg.qdrant_url} "
                    f"(collection '{cfg.qdrant_collection}', dashboard {cfg.qdrant_url}/dashboard)"
                )
            else:
                CLI.warning(
                    f"Qdrant non raggiungibile su {cfg.qdrant_url} — "
                    f"avvia con ~/.ltsia/qdrant/start.sh oppure disabilita in ltsia.ini"
                )
        except Exception as e:
            CLI.warning(f"Qdrant non inizializzato: {e}")

    def _init_tiered_memory(self):
        """Inizializza short-term (RAM) e medium-term (SQLite)."""
        cfg = self.config
        try:
            persist_dir = str(Path.home() / ".ltsia" / "scopes")
            self.short_term = ShortTermMemory(
                max_drawers=cfg.memory_short_drawers,
                persist_dir=persist_dir,
            )
        except Exception as e:
            CLI.warning(f"ShortTermMemory non inizializzata: {e}")

        try:
            db_path = str(Path.home() / ".ltsia" / "memory_medium.db")
            self.medium_term = MediumTermMemory(
                db_path=db_path,
                ttl_days=cfg.memory_medium_ttl_days,
            )
        except Exception as e:
            CLI.warning(f"MediumTermMemory non inizializzata: {e}")

    def _init_memory_orchestrator(self):
        """Crea MemoryOrchestratorAgent con searcher + reader ASMR."""
        cfg = self.config
        model = cfg.exec_model

        # Long-term preferito: Qdrant, fallback SemanticMemory legacy
        long_term = self.qdrant_memory or self.semantic_memory

        self.memory_reader = MemoryReaderAgent(client=self.openai, model=model)
        self.memory_searcher = MemorySearcherAgent(
            client=self.openai,
            model=model,
            short_term=self.short_term,
            medium_term=self.medium_term,
            long_term=long_term,
        )

        # web search delegate: usa SearchAgent
        def _web(query: str) -> str:
            try:
                return self.search_agent.search(query)
            except Exception as e:
                return f"(ricerca web fallita: {e})"

        self.memory_orchestrator = MemoryOrchestratorAgent(
            permanent_memory=self.memory,
            short_term=self.short_term,
            medium_term=self.medium_term,
            long_term=long_term,
            searcher=self.memory_searcher,
            reader=self.memory_reader,
            web_search_delegate=_web,
            web_fallback_threshold=cfg.memory_web_fallback_threshold,
        )
        CLI.success("MemoryOrchestrator pronto (short + medium + long + web fallback)")

    def _wire_memory_orchestrator(self):
        """Attacca l'orchestratore al ChatAgent con hook pre/post-turn."""
        if not hasattr(self, "chat_agent") or not self.memory_orchestrator:
            return
        self.chat_agent.set_memory_orchestrator(self.memory_orchestrator)

    def _init_semantic_memory(self):
        """Inizializza la memoria semantica se embedding_host è configurato."""
        cfg = self.config
        if not cfg.embedding_host:
            return
        try:
            from src.memory.embedding_client import EmbeddingClient
            from src.memory.semantic_memory import SemanticMemory
            embedder = EmbeddingClient(
                host=cfg.embedding_host,
                port=cfg.embedding_port,
                model=cfg.embedding_model,
                api_type=getattr(cfg, "embedding_api", "ollama"),
            )
            db_path = str(Path.home() / ".ltsia" / "semantic_memory.db")
            self.semantic_memory = SemanticMemory(db_path, "ltsia", embedder)
            CLI.success(f"Memoria semantica: {cfg.embedding_host}:{cfg.embedding_port} ({cfg.embedding_model})")
        except Exception as e:
            CLI.warning(f"Memoria semantica non disponibile: {e}")

    def _init_job_manager(self):
        """Inizializza il JobManager e ripristina i job attivi."""
        try:
            from src.jobs.job_manager import JobManager
            jobs_dir = str(Path.home() / ".ltsia" / "jobs")
            jm = JobManager(jobs_dir)
            jm.restore_active_jobs()
            return jm
        except Exception as e:
            CLI.warning(f"JobManager non disponibile: {e}")
            return None

    def _init_agents(self):
        cfg = self.config
        model = cfg.exec_model  # tutti gli agenti usano qwen3-instruct

        # Contesto iniziale da ContextAgent
        session_ctx = self.context_agent.build_session_context()
        if session_ctx.estimated_tokens > 0:
            CLI.info(
                f"Contesto iniziale: ~{session_ctx.estimated_tokens} token "
                f"({len(session_ctx.semantic_hits)} memorie semantiche, "
                f"{len(session_ctx.permanent_hits)} permanenti)"
            )

        self.chat_agent = ChatAgent(
            client=self.openai,
            model=model,
            registry=self.registry,
            memory=self.memory,
            work_dir=cfg.work_dir,
            context_window=cfg.context_window,
            initial_context=session_ctx.text,
        )
        self.pm_agent = ProjectManagerAgent(
            client=self.openai,
            model=model,
            registry=self.registry,
            context_window=cfg.context_window,
        )
        self.exec_agent = ExecutionAgent(
            client=self.openai,
            model=model,
            on_stream=lambda chunk: print(chunk, end="", flush=True),
        )
        self.testing_agent = TestingAgent(
            client=self.openai,
            model=model,
            registry=self.registry,
            max_retries=cfg.max_test_retries,
            context_window=cfg.context_window,
        )
        self.cli_test_agent = CLITestAgent(
            client=self.openai,
            model=model,
            registry=self.registry,
            context_window=cfg.context_window,
        )
        self.search_agent = SearchAgent(
            client=self.openai,
            model=model,
            registry=self.registry,
            context_window=cfg.context_window,
        )

    def _wire_delegates(self):
        """Collega pseudo-tool ai rispettivi agenti."""

        def planning_delegate(args: dict) -> str:
            task = args.get("task", "")
            CLI.step(f"ProjectManagerAgent: pianificazione...")
            return self.pm_agent.plan(task)

        def file_creation_delegate(args: dict) -> str:
            path = args.get("path", "")
            description = args.get("description", "")
            context_info = args.get("context", "")
            CLI.step(f"ExecutionAgent: creazione {path}...")
            result = self.exec_agent.create_file(
                path=path,
                description=description,
                context_info=context_info,
                work_dir=self.config.work_dir,
            )
            if not result.startswith("ERROR"):
                CLI.success(f"File creato: {result}")
            else:
                CLI.error(result)
            return result

        def testing_delegate(args: dict) -> str:
            project_dir = args.get("project_dir", self.config.work_dir)
            test_command = args.get("test_command", "")
            # Rileva tipo progetto
            if self._is_cli_project(project_dir):
                CLI.step("CLITestAgent: test CLI...")
                return self.cli_test_agent.test(project_dir, test_command)
            else:
                CLI.step("TestingAgent: test web...")
                result = self.testing_agent.test(test_command or "npm test", project_dir)
                return json.dumps(result, ensure_ascii=False)

        def search_delegate(args: dict) -> str:
            query = args.get("query", "")
            CLI.step(f"SearchAgent: ricerca '{query}'...")
            return self.search_agent.search(query)

        self.registry.set_planning_delegate(planning_delegate)
        self.registry.set_file_creation_delegate(file_creation_delegate)
        self.registry.set_testing_delegate(testing_delegate)
        self.registry.set_search_delegate(search_delegate)

        # Output callback per streaming comandi
        self.registry.set_command_output_callback(
            lambda line: print(CLI.dim("  " + line.rstrip()), flush=True)
        )

    def _is_cli_project(self, directory: str) -> bool:
        """Rileva se il progetto è CLI o web."""
        web_markers = ["package.json", "index.html", "index.php", "artisan"]
        for marker in web_markers:
            if os.path.exists(os.path.join(directory, marker)):
                return False
        # Se ci sono file .py/.php senza index
        for ext in [".py", ".php", ".go", ".rb"]:
            import glob
            if glob.glob(os.path.join(directory, f"*{ext}")):
                return True
        return True  # default CLI

    def run(self, task: str, test_command: str = "") -> int:
        """Modalità one-shot. Ritorna exit code."""
        logger = SessionLogger(mode="oneshot", task=task, work_dir=self.config.work_dir)

        CLI.header("LTSIA — modalità one-shot")
        CLI.info(f"Task: {task}")

        # Verifica connettività
        if not self._check_connectivity():
            return 1

        CLI.step("ChatAgent: elaborazione task...")
        try:
            response = self.chat_agent.chat(
                task,
                on_stream=lambda chunk: print(chunk, end="", flush=True),
            )
            print("\n")
        except Exception as e:
            CLI.error(f"Errore ChatAgent: {e}")
            logger.error(str(e))
            logger.flush()
            return 2

        stats = self.chat_agent.get_stats()
        logger.add_tokens(stats.get("total_prompt_tokens", 0) + stats.get("total_completion_tokens", 0))

        # Test opzionale
        if test_command:
            CLI.step(f"Esecuzione test: {test_command}")
            project_dir = self._find_project_dir()
            result = self.testing_agent.test(test_command, project_dir)
            if result.get("success"):
                CLI.success("Test passati")
                logger.test_result(True, result.get("summary", ""))
            else:
                CLI.error("Test falliti")
                logger.test_result(False, result.get("summary", ""))
                logger.flush()
                return 2

        logger.flush()
        return 0

    def interactive(self):
        """Modalità REPL interattiva."""
        from src.ui.interactive import Interactive

        if not self._check_connectivity():
            CLI.warning("Connettività LLM non disponibile — avvio in modalità limitata")

        voice_status = self._start_voice_background(8765)

        repl = Interactive(
            self.chat_agent,
            self.registry,
            self.config.work_dir,
            voice_status=voice_status,
            job_manager=self.job_manager,
        )
        repl.run()

    def voice(self, port: int = 8765) -> int:
        """Modalità voice standalone: avvia solo il WebSocket server (bloccante)."""
        from src.voice.voice_server import VoiceServer
        from src.voice.tts import resolve_tts_voice

        CLI.header("LTSIA — modalità voice")

        if not self._check_connectivity():
            CLI.error("LLM non raggiungibile — impossibile avviare la modalità voice")
            return 1

        tts_voice = resolve_tts_voice(self.config.tts_voice)
        if tts_voice:
            CLI.success(f"edge-tts attivo — voce: {tts_voice}")
        else:
            CLI.info("edge-tts non trovato — il client userà TTS browser")
            CLI.info("Per TTS di qualità: pip install edge-tts")

        CLI.info("Avvia il voice tool React:  cd voice && npm run dev")

        server = VoiceServer(self.chat_agent, self.config, job_manager=self.job_manager)
        server.run(port)
        return 0

    def _start_voice_background(self, port: int = 8765) -> str:
        """
        Avvia VoiceServer in un thread daemon con un ChatAgent dedicato.
        Ritorna una stringa di status per il banner, o '' se websockets non disponibile.
        """
        try:
            import websockets  # noqa: F401
        except ImportError:
            return ""

        from src.voice.voice_server import VoiceServer
        from src.voice.tts import resolve_tts_voice
        from src.http.openai_client import OpenAIClient
        from src.http.ptc_adapter import PTCAdapter
        from src.agents.chat_agent import ChatAgent
        import threading

        # ChatAgent separato — voice è una sessione distinta dal REPL
        voice_client = PTCAdapter(OpenAIClient(self.config.exec_base_url))
        voice_agent = ChatAgent(
            client=voice_client,
            model=self.config.exec_model,
            registry=self.registry,
            memory=self.memory,
            work_dir=self.config.work_dir,
            context_window=self.config.context_window,
        )

        server = VoiceServer(voice_agent, self.config, job_manager=self.job_manager)
        t = threading.Thread(target=server.run, args=(port,), daemon=True)
        t.start()

        # Aspetta che il voice server abbia terminato il preload (Whisper, ECAPA)
        # e sia in ascolto. Timeout generoso per CPU lente.
        if not server.ready.wait(timeout=120):
            CLI.warning("Voice server: timeout 120s sull'avvio — continuo con REPL")

        tts_voice = resolve_tts_voice(self.config.tts_voice)
        label = f"ws://localhost:{port}"
        label += f" · edge-tts: {tts_voice}" if tts_voice else " · TTS browser"
        return label

    def _check_connectivity(self) -> bool:
        exec_ok = self.openai.ping()
        emb_ok = self.ollama.ping()

        if not exec_ok:
            CLI.warning(f"LLM non raggiungibile: {self.config.exec_base_url}")
        else:
            CLI.success(
                f"LLM: {self.config.exec_base_url} "
                f"({self.config.exec_model}, ctx {self.config.context_window // 1024}k)"
            )

        if emb_ok and self.config.embedding_host:
            CLI.success(f"Embedding: {self.config.embedding_base_url} ({self.config.embedding_model})")

        return exec_ok

    def doctor(self):
        """Diagnostica connettività e configurazione."""
        CLI.header("LTSIA Doctor")
        self._check_connectivity()
        CLI.info(f"Python: {sys.version}")
        CLI.info(f"Modello: {self.config.exec_model} — context window: {self.config.context_window // 1024}k token")
        CLI.info(f"Context budget: warn {self.config.context_initial_warn_tokens} / max {self.config.context_initial_max_tokens} token")
        CLI.info(f"Work dir: {self.config.work_dir}")
        CLI.info(f"Extensions dir: {self.extensions_dir}")
        tools = self.registry.list_tools()
        CLI.info(f"Tool caricati: {len(tools)}")
        if self.qdrant_memory and self.qdrant_memory.is_ready():
            CLI.success(f"Memoria lungo termine (Qdrant): {self.qdrant_memory.count()} voci · dashboard {self.config.qdrant_url}/dashboard")
        elif self.semantic_memory:
            CLI.success("Memoria semantica legacy: attiva (Qdrant non disponibile)")
        else:
            CLI.warning("Memoria semantica: non configurata (imposta embedding_host + avvia qdrant)")

        if self.medium_term:
            try:
                s = self.medium_term.stats()
                CLI.info(
                    f"Memoria medio termine: {s['wings']} wing · {s['halls']} hall · "
                    f"{s['rooms']} room · {s['drawers']} drawer · {s['closets']} closet"
                )
            except Exception:
                pass

        if self.short_term:
            CLI.info(f"Memoria brevissima: attiva (max {self.config.memory_short_drawers} drawer per scope)")

        if hasattr(self, "memory_orchestrator") and self.memory_orchestrator:
            CLI.info(
                f"Fallback web soglia: {self.config.memory_web_fallback_threshold} · "
                f"TTL medio termine: {self.config.memory_medium_ttl_days}g"
            )
        for pkg in ["requests"]:
            try:
                __import__(pkg)
                CLI.success(f"Pacchetto '{pkg}': OK")
            except ImportError:
                CLI.error(f"Pacchetto '{pkg}': MANCANTE — esegui: pip install {pkg}")

    def _find_project_dir(self) -> str:
        """Trova subdirectory del progetto generato in work_dir."""
        work = self.config.work_dir
        try:
            dirs = [
                d for d in os.listdir(work)
                if os.path.isdir(os.path.join(work, d)) and not d.startswith(".")
            ]
            if len(dirs) == 1:
                return os.path.join(work, dirs[0])
        except Exception:
            pass
        return work
