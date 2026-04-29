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
from src.memory.core_facts import CoreFactsMemory
from src.memory.short_term import ShortTermMemory
from src.memory.medium_term import MediumTermMemory
from src.memory.qdrant_memory import QdrantMemory
from src.memory.qdrant_lifecycle import QdrantLifecycle
from src.agents.chat_agent import ChatAgent
from src.agents.context_agent import ContextAgent
from src.agents.memory_orchestrator_agent import MemoryOrchestratorAgent
from src.agents.memory_reader_agent import MemoryReaderAgent
from src.agents.memory_searcher_agent import MemorySearcherAgent
from src.agents.memory_optimizer_agent import MemoryOptimizerAgent
from src.memory.promotion_service import PromotionService
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
        self.planning_ollama = OllamaClient(config.thinking_base_url, timeout=config.ollama_timeout)
        self.openai = PTCAdapter(OpenAIClient(config.exec_base_url))

        CLI.step("Inizializzazione memoria...")
        self.memory = CoreFactsMemory()
        self.semantic_memory = None          # legacy SQLite (fallback)
        self.qdrant_memory = None            # long-term Qdrant
        self.qdrant_lifecycle = None         # gestore avvio/spegnimento Qdrant
        self.short_term = None               # brevissimo termine (RAM)
        self.medium_term = None              # medio termine (MemPalace SQLite)
        self._init_semantic_memory()
        self._ensure_qdrant_running()
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

        # Aggiorna tool memoria con tutti i riferimenti ora disponibili
        self.registry.register_memory_tools(
            core_facts=self.memory,
            qdrant_memory=self.qdrant_memory if (self.qdrant_memory and self.qdrant_memory.is_ready()) else None,
            medium_term=self.medium_term,
        )

        # Registra search_memory (ricerca semantica)
        active_semantic = self.qdrant_memory if (self.qdrant_memory and self.qdrant_memory.is_ready()) else self.semantic_memory
        if active_semantic:
            self.registry.register_semantic_memory(active_semantic)

        # Inietta il client Qdrant nel viz tool (necessario in qdrant_mode=local)
        if self.qdrant_memory and self.qdrant_memory.is_ready():
            self.registry.register_qdrant_memory(self.qdrant_memory)

        # Job manager
        self.job_manager = self._init_job_manager()
        if self.job_manager:
            self.registry.register_job_manager(self.job_manager)

        CLI.step("Inizializzazione ContextAgent...")
        self.context_agent = ContextAgent(
            permanent_memory=self.memory,
            semantic_memory=self.semantic_memory,
            qdrant_memory=self.qdrant_memory,
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

    def _ensure_qdrant_running(self):
        """Avvia Qdrant locale se non già in ascolto.
        In modalità 'local' salta il lifecycle: il client usa storage embedded."""
        cfg = self.config
        if not cfg.embedding_host:
            return
        if getattr(cfg, "qdrant_mode", "server") == "local":
            return  # nessun server da avviare
        lifecycle = QdrantLifecycle(cfg.qdrant_host, cfg.qdrant_port)
        ok, status = lifecycle.start_if_needed()
        self.qdrant_lifecycle = lifecycle
        if status == "started" and ok:
            CLI.success(f"Qdrant avviato (PID {lifecycle.process.pid}) su {cfg.qdrant_url}")
        elif status == "already":
            pass
        elif status == "missing":
            CLI.warning(
                f"Binario Qdrant non trovato in ~/.ltsia/qdrant/qdrant — "
                f"avvio automatico saltato"
            )
        elif status == "timeout":
            CLI.warning(f"Qdrant avviato ma non risponde entro il timeout su {cfg.qdrant_url}")
        else:
            CLI.warning(f"Qdrant non avviato ({status})")

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
            qdrant_mode = getattr(cfg, "qdrant_mode", "server")
            qm = QdrantMemory(
                host=cfg.qdrant_host,
                port=cfg.qdrant_port,
                collection=cfg.qdrant_collection,
                vector_size=cfg.qdrant_vector_size,
                embedder=embedder,
                dedup_threshold=cfg.memory_dedup_threshold,
                mode=qdrant_mode,
            )
            if qm.is_ready():
                self.qdrant_memory = qm
                if qdrant_mode == "local":
                    CLI.success(
                        f"Qdrant memoria lungo termine: modalità locale "
                        f"(~/.ltsia/qdrant/storage, collection '{cfg.qdrant_collection}')"
                    )
                else:
                    CLI.success(
                        f"Qdrant memoria lungo termine: {cfg.qdrant_url} "
                        f"(collection '{cfg.qdrant_collection}', dashboard {cfg.qdrant_url}/dashboard)"
                    )
            else:
                CLI.warning(
                    f"Qdrant non inizializzato — "
                    f"verifica qdrant_mode in ltsia.ini (server|local)"
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

        # PromotionService: promuove voci medium → Qdrant prima della scadenza (48h)
        self.promotion_service = None
        if self.medium_term and self.qdrant_memory and self.qdrant_memory.is_ready():
            try:
                self.promotion_service = PromotionService(
                    medium_term=self.medium_term,
                    qdrant_memory=self.qdrant_memory,
                    llm_client=self.openai,
                    llm_model=cfg.exec_model,
                    verbose=cfg.verbose,
                )
                self.promotion_service.start_background()
                CLI.success("PromotionService avviato (medium → Qdrant ogni 30 min)")
            except Exception as e:
                CLI.warning(f"PromotionService non avviato: {e}")

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
            client=self.planning_ollama,
            model=cfg.planning_model,
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

    def service(self):
        """Modalità servizio: avvia tutti i background worker senza REPL e blocca."""
        import atexit
        import signal

        if not self._check_connectivity():
            CLI.warning("Connettività LLM non disponibile — avvio in modalità limitata")

        self._voice_port = 8765
        voice_status = self._start_voice_background(self._voice_port)
        if voice_status:
            CLI.success(f"Voice server: {voice_status}")

        optimizer_status = self._start_memory_optimizer_background()
        if optimizer_status:
            CLI.success(f"Memory optimizer: {optimizer_status}")
        atexit.register(self._stop_memory_optimizer)

        telegram_status = self._start_telegram_background()
        if telegram_status:
            CLI.success(f"Telegram bot: {telegram_status}")
        atexit.register(self._stop_telegram)

        CLI.success("LTSIA service avviato — in attesa (Ctrl+C per terminare)")

        stop_event = __import__("threading").Event()

        def _shutdown(signum, frame):
            CLI.info("Segnale ricevuto — spegnimento in corso…")
            stop_event.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        stop_event.wait()

        vs = getattr(self, "_voice_server", None)
        if vs is not None:
            vs.stop()
        self._stop_memory_optimizer()
        self._stop_telegram()
        CLI.info("LTSIA service terminato.")

    def interactive(self):
        """Modalità REPL interattiva."""
        from src.ui.interactive import Interactive
        import atexit

        if not self._check_connectivity():
            CLI.warning("Connettività LLM non disponibile — avvio in modalità limitata")

        self._voice_port = 8765
        voice_status = self._start_voice_background(self._voice_port)

        optimizer_status = self._start_memory_optimizer_background()
        if optimizer_status:
            CLI.success(f"Memory optimizer: {optimizer_status}")
        atexit.register(self._stop_memory_optimizer)

        telegram_status = self._start_telegram_background()
        if telegram_status:
            CLI.success(f"Telegram bot: {telegram_status}")
        atexit.register(self._stop_telegram)

        repl = Interactive(
            self.chat_agent,
            self.registry,
            self.config.work_dir,
            voice_status=voice_status,
            job_manager=self.job_manager,
            telegram_start_callback=self._setup_and_start_telegram,
        )
        try:
            repl.run()
        finally:
            vs = getattr(self, "_voice_server", None)
            if vs is not None:
                vs.stop()
            self._stop_memory_optimizer()

    def _start_memory_optimizer_background(self) -> str:
        """Avvia il MemoryOptimizerAgent in background. Ritorna label status o ''."""
        cfg = self.config
        if not getattr(cfg, "memory_optimizer_enabled", True):
            return ""
        if not (self.qdrant_memory and self.qdrant_memory.is_ready()):
            return ""
        try:
            agent = MemoryOptimizerAgent(
                client=self.openai,
                model=cfg.exec_model,
                qdrant_memory=self.qdrant_memory,
                interval=cfg.memory_optimizer_interval,
                batch_size=cfg.memory_optimizer_batch,
                merge_threshold=cfg.memory_optimizer_merge_threshold,
                auto_merge_threshold=cfg.memory_optimizer_auto_merge_threshold,
                split_min_chars=cfg.memory_optimizer_split_min_chars,
            )
            agent.start_background()
        except Exception as e:
            CLI.warning(f"Memory optimizer non avviato: {e}")
            return ""
        self.memory_optimizer = agent
        minutes = cfg.memory_optimizer_interval // 60
        return (
            f"attivo ogni {minutes}m · batch {cfg.memory_optimizer_batch} · "
            f"dedup >= {cfg.memory_dedup_threshold}"
        )

    def _stop_memory_optimizer(self):
        agent = getattr(self, "memory_optimizer", None)
        if agent:
            try:
                agent.stop()
            except Exception:
                pass
        ps = getattr(self, "promotion_service", None)
        if ps:
            try:
                ps.stop()
            except Exception:
                pass

    def _start_telegram_background(self) -> str:
        """Avvia il bot Telegram in un thread daemon. Ritorna label status o ''."""
        cfg = self.config
        if not cfg.telegram_token:
            return ""
        try:
            from src.telegram.telegram_bot import TelegramBot
            from src.http.openai_client import OpenAIClient
            from src.http.ptc_adapter import PTCAdapter
            from src.agents.chat_agent import ChatAgent
            import threading

            # ChatAgent dedicato — sessione Telegram separata dal REPL
            tg_client = PTCAdapter(OpenAIClient(cfg.exec_base_url))
            tg_agent = ChatAgent(
                client=tg_client,
                model=cfg.exec_model,
                registry=self.registry,
                memory=self.memory,
                work_dir=cfg.work_dir,
                context_window=cfg.context_window,
            )

            bot = TelegramBot(
                token=cfg.telegram_token,
                chat_agent=tg_agent,
                config=cfg,
                voice_reply=cfg.telegram_voice_reply,
                language=cfg.telegram_language,
                allowed_chat_ids=cfg.telegram_allowed_ids,
            )
            t = threading.Thread(target=bot.run, daemon=True, name="telegram-bot")
            t.start()
            self._telegram_bot = bot
            ids_label = f" · ids: {cfg.telegram_allowed_ids}" if cfg.telegram_allowed_ids else " · aperto a tutti"
            return f"attivo · voce: {'sì' if cfg.telegram_voice_reply else 'no'}{ids_label}"
        except Exception as e:
            CLI.warning(f"Telegram bot non avviato: {e}")
            return ""

    def _stop_telegram(self):
        bot = getattr(self, "_telegram_bot", None)
        if bot:
            try:
                bot.stop()
            except Exception:
                pass

    def _setup_and_start_telegram(self, token: str, voice_reply: bool, allowed_ids_str: str) -> str:
        """
        Chiamato dal wizard interattivo: aggiorna config in memoria e avvia il bot.
        Ritorna label status o '' se fallisce.
        """
        # Ferma eventuale bot già in esecuzione
        self._stop_telegram()
        self._telegram_bot = None

        # Aggiorna config in memoria
        self.config.telegram_token = token
        self.config.telegram_voice_reply = voice_reply
        self.config.telegram_allowed_ids = [
            int(x.strip()) for x in allowed_ids_str.split(",") if x.strip().isdigit()
        ]

        return self._start_telegram_background()

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

        server = VoiceServer(
            self.chat_agent,
            self.config,
            job_manager=self.job_manager,
            snapshot_provider=self.build_snapshot,
        )
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

        qdrant_viz_backend = None
        if self.qdrant_memory and self.qdrant_memory.is_ready():
            try:
                from src.tools.qdrant_viz.viz_tool import build_backend
                qdrant_viz_backend = build_backend(self.config, self.qdrant_memory)
            except Exception:
                pass

        server = VoiceServer(
            voice_agent,
            self.config,
            job_manager=self.job_manager,
            snapshot_provider=self.build_snapshot,
            qdrant_viz_backend=qdrant_viz_backend,
        )
        t = threading.Thread(target=server.run, args=(port,), daemon=True)
        t.start()
        self._voice_server = server

        # Aspetta che il voice server abbia terminato il preload (Whisper, ECAPA)
        # e sia in ascolto. Timeout generoso per CPU lente.
        if not server.ready.wait(timeout=120):
            CLI.warning("Voice server: timeout 120s sull'avvio — continuo con REPL")

        server.print_startup_banner()

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
        if self.config.telegram_token:
            CLI.success(f"Telegram bot: token configurato · lingua STT: {self.config.telegram_language}")
        else:
            CLI.info("Telegram bot: non configurato (imposta telegram_token in ltsia.ini o LTSIA_TELEGRAM_TOKEN)")
        for pkg in ["requests"]:
            try:
                __import__(pkg)
                CLI.success(f"Pacchetto '{pkg}': OK")
            except ImportError:
                CLI.error(f"Pacchetto '{pkg}': MANCANTE — esegui: pip install {pkg}")

    def build_snapshot(self) -> dict:
        """Ritorna snapshot per la dashboard voice: modello, agenti, link, moduli, job."""
        cfg = self.config

        agents: list[str] = []
        for attr, label in [
            ("chat_agent",          "ChatAgent"),
            ("pm_agent",            "ProjectManagerAgent"),
            ("exec_agent",          "ExecutionAgent"),
            ("testing_agent",       "TestingAgent"),
            ("cli_test_agent",      "CLITestAgent"),
            ("search_agent",        "SearchAgent"),
            ("context_agent",       "ContextAgent"),
            ("memory_reader",       "MemoryReaderAgent"),
            ("memory_searcher",     "MemorySearcherAgent"),
            ("memory_orchestrator", "MemoryOrchestratorAgent"),
            ("memory_optimizer",    "MemoryOptimizerAgent"),
        ]:
            if getattr(self, attr, None) is not None:
                agents.append(label)

        ext_dir = Path(getattr(self, "extensions_dir", ""))
        modules: list[str] = []
        if ext_dir.is_dir():
            for f in sorted(ext_dir.iterdir()):
                if f.suffix == ".py" and not f.name.startswith("_"):
                    modules.append(f.stem)

        links: list[dict] = [
            {"label": "LLM exec",      "url": cfg.exec_base_url},
            {"label": "Embedding",     "url": cfg.embedding_base_url},
        ]
        if self.qdrant_memory and self.qdrant_memory.is_ready():
            links.append({"label": "Qdrant dashboard", "url": f"{cfg.qdrant_url}/dashboard"})
        if getattr(self, "_viz_url", ""):
            links.append({"label": "Qdrant Viz", "url": self._viz_url})
        voice_port = getattr(self, "_voice_port", 8765)
        links.append({"label": "Voice WS", "url": f"ws://localhost:{voice_port}"})

        jobs: list[dict] = []
        if self.job_manager:
            try:
                for j in self.job_manager.list_jobs():
                    jobs.append(j.to_dict())
            except Exception:
                pass

        job_logs: list[dict] = []
        if self.job_manager:
            try:
                job_logs = self.job_manager.get_output_history(limit=30)
            except Exception:
                job_logs = []

        return {
            "model": {
                "name":           cfg.exec_model,
                "context_window": cfg.context_window,
                "url":            cfg.exec_base_url,
            },
            "thinking_model": {
                "name": cfg.thinking_model,
                "url":  f"http://{cfg.thinking_host}:{cfg.thinking_port}",
            },
            "planning_model": {
                "name": cfg.planning_model,
                "url":  f"http://{cfg.thinking_host}:{cfg.thinking_port}",
            },
            "agents":  {"count": len(agents),  "names": agents},
            "modules": {"count": len(modules), "names": modules},
            "links":   links,
            "jobs":    jobs,
            "job_logs": job_logs,
        }

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
