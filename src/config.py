"""
Configuration — lettura a cascata: CLI flag > ./ltsia.ini > ~/.ltsia/config.ini > env var > default
"""
from __future__ import annotations
import os
import configparser
from pathlib import Path
from typing import Optional


class Config:
    def __init__(
        self,
        thinking_host: str = "192.168.250.203",
        thinking_port: int = 11434,
        thinking_model: str = "qwen3-instruct",
        exec_host: str = "10.149.245.212",
        exec_port: int = 8807,
        exec_model: str = "qwen3-instruct",
        context_window: int = 131072,
        compaction_threshold: float = 0.75,
        context_initial_warn_tokens: int = 6000,
        context_initial_max_tokens: int = 8000,
        max_iterations: int = 30,
        max_test_retries: int = 5,
        work_dir: str = "/tmp/sandbox",
        embedding_host: str = "",
        embedding_port: int = 11434,
        embedding_model: str = "nomic-embed-text",
        db_host: str = "localhost",
        db_port: int = 5432,
        db_name: str = "",
        db_user: str = "",
        db_password: str = "",
        db_ssl_mode: str = "prefer",
        tts_enabled: bool = False,
        tts_voice: str = "it-IT-IsabellaNeural",
        tts_rate: str = "+20%",
        supermemory_api_key: str = "",
        supermemory_space_id: str = "",
        ollama_timeout: int = 1800,
        update_check_url: str = "",
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        qdrant_collection: str = "ltsia_longterm",
        qdrant_vector_size: int = 768,
        memory_web_fallback_threshold: float = 0.5,
        memory_medium_ttl_days: int = 30,
        memory_short_drawers: int = 20,
        memory_dedup_threshold: float = 0.93,
        memory_optimizer_enabled: bool = True,
        memory_optimizer_interval: int = 900,
        memory_optimizer_batch: int = 30,
        memory_optimizer_merge_threshold: float = 0.87,
        memory_optimizer_auto_merge_threshold: float = 0.97,
        memory_optimizer_split_min_chars: int = 120,
        telegram_token: str = "",
        telegram_voice_reply: bool = True,
        telegram_language: str = "it",
        telegram_allowed_ids: str = "",
    ):
        self.thinking_host = thinking_host
        self.thinking_port = thinking_port
        self.thinking_model = thinking_model
        self.exec_host = exec_host
        self.exec_port = exec_port
        self.exec_model = exec_model
        self.context_window = context_window
        self.compaction_threshold = compaction_threshold
        self.context_initial_warn_tokens = context_initial_warn_tokens
        self.context_initial_max_tokens = context_initial_max_tokens
        self.max_iterations = max_iterations
        self.max_test_retries = max_test_retries
        self.work_dir = work_dir
        self.embedding_host = embedding_host or thinking_host
        self.embedding_port = embedding_port
        self.embedding_model = embedding_model
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_ssl_mode = db_ssl_mode
        self.tts_enabled = tts_enabled
        self.tts_voice = tts_voice
        self.tts_rate = tts_rate
        self.supermemory_api_key = supermemory_api_key
        self.supermemory_space_id = supermemory_space_id
        self.ollama_timeout = ollama_timeout
        self.update_check_url = update_check_url
        self.qdrant_host = qdrant_host
        self.qdrant_port = qdrant_port
        self.qdrant_collection = qdrant_collection
        self.qdrant_vector_size = qdrant_vector_size
        self.memory_web_fallback_threshold = memory_web_fallback_threshold
        self.memory_medium_ttl_days = memory_medium_ttl_days
        self.memory_short_drawers = memory_short_drawers
        self.memory_dedup_threshold = memory_dedup_threshold
        self.memory_optimizer_enabled = memory_optimizer_enabled
        self.memory_optimizer_interval = memory_optimizer_interval
        self.memory_optimizer_batch = memory_optimizer_batch
        self.memory_optimizer_merge_threshold = memory_optimizer_merge_threshold
        self.memory_optimizer_auto_merge_threshold = memory_optimizer_auto_merge_threshold
        self.memory_optimizer_split_min_chars = memory_optimizer_split_min_chars
        self.telegram_token = telegram_token or os.environ.get("LTSIA_TELEGRAM_TOKEN", "")
        self.telegram_voice_reply = telegram_voice_reply
        self.telegram_language = telegram_language
        # "123456,789012" → [123456, 789012]
        self.telegram_allowed_ids: list[int] = [
            int(x.strip()) for x in telegram_allowed_ids.split(",") if x.strip().isdigit()
        ]

    @property
    def thinking_base_url(self) -> str:
        return f"http://{self.thinking_host}:{self.thinking_port}"

    @property
    def exec_base_url(self) -> str:
        return f"http://{self.exec_host}:{self.exec_port}"

    @property
    def embedding_base_url(self) -> str:
        return f"http://{self.embedding_host}:{self.embedding_port}"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @property
    def compaction_token_limit(self) -> int:
        return int(self.context_window * self.compaction_threshold)

    @property
    def semantic_memory_db_path(self) -> str:
        return str(Path.home() / ".ltsia" / "memory_semantic.db")

    @staticmethod
    def load(cli_overrides: Optional[dict] = None) -> "Config":
        """Carica config a cascata: CLI > ./ltsia.ini > ~/.ltsia/config.ini > env > default"""
        cfg = Config()
        # 1. env vars
        cfg._load_env()
        # 2. global ini
        global_ini = Path.home() / ".ltsia" / "config.ini"
        if global_ini.exists():
            cfg._load_ini(str(global_ini))
        # 3. local ini
        local_ini = Path("ltsia.ini")
        if local_ini.exists():
            cfg._load_ini(str(local_ini))
        # 4. CLI overrides
        if cli_overrides:
            cfg._apply_dict(cli_overrides)
        return cfg

    def _load_env(self):
        mapping = {
            "LTSIA_THINKING_HOST": "thinking_host",
            "LTSIA_THINKING_PORT": ("thinking_port", int),
            "LTSIA_THINKING_MODEL": "thinking_model",
            "LTSIA_EXECUTION_HOST": "exec_host",
            "LTSIA_EXECUTION_PORT": ("exec_port", int),
            "LTSIA_EXECUTION_MODEL": "exec_model",
            "LTSIA_CONTEXT_WINDOW": ("context_window", int),
            "LTSIA_WORK_DIR": "work_dir",
        }
        for env_key, attr in mapping.items():
            val = os.environ.get(env_key)
            if val:
                if isinstance(attr, tuple):
                    name, cast = attr
                    setattr(self, name, cast(val))
                else:
                    setattr(self, attr, val)

    def _load_ini(self, path: str):
        parser = configparser.RawConfigParser()
        parser.read(path)
        section = "ltsia" if "ltsia" in parser else "DEFAULT"
        data = dict(parser[section]) if section in parser else {}
        ini_map = {
            "thinking_host": "thinking_host",
            "thinking_port": ("thinking_port", int),
            "thinking_model": "thinking_model",
            "exec_host": "exec_host",
            "execution_host": "exec_host",
            "exec_port": ("exec_port", int),
            "execution_port": ("exec_port", int),
            "exec_model": "exec_model",
            "execution_model": "exec_model",
            "context_window": ("context_window", int),
            "compaction_threshold": ("compaction_threshold", float),
            "max_iterations": ("max_iterations", int),
            "max_test_retries": ("max_test_retries", int),
            "context_initial_warn_tokens": ("context_initial_warn_tokens", int),
            "context_initial_max_tokens": ("context_initial_max_tokens", int),
            "work_dir": "work_dir",
            "embedding_host": "embedding_host",
            "embedding_port": ("embedding_port", int),
            "embedding_model": "embedding_model",
            "db_host": "db_host",
            "db_port": ("db_port", int),
            "db_name": "db_name",
            "db_user": "db_user",
            "db_password": "db_password",
            "db_ssl_mode": "db_ssl_mode",
            "tts_enabled": ("tts_enabled", lambda v: v.lower() in ("1", "true", "yes")),
            "tts_voice": "tts_voice",
            "tts_rate": "tts_rate",
            "supermemory_api_key": "supermemory_api_key",
            "supermemory_space_id": "supermemory_space_id",
            "ollama_timeout": ("ollama_timeout", int),
            "update_check_url": "update_check_url",
            "qdrant_host": "qdrant_host",
            "qdrant_port": ("qdrant_port", int),
            "qdrant_collection": "qdrant_collection",
            "qdrant_vector_size": ("qdrant_vector_size", int),
            "memory_web_fallback_threshold": ("memory_web_fallback_threshold", float),
            "memory_medium_ttl_days": ("memory_medium_ttl_days", int),
            "memory_short_drawers": ("memory_short_drawers", int),
            "memory_dedup_threshold": ("memory_dedup_threshold", float),
            "memory_optimizer_enabled": ("memory_optimizer_enabled", lambda v: v.lower() in ("1", "true", "yes")),
            "memory_optimizer_interval": ("memory_optimizer_interval", int),
            "memory_optimizer_batch": ("memory_optimizer_batch", int),
            "memory_optimizer_merge_threshold": ("memory_optimizer_merge_threshold", float),
            "memory_optimizer_auto_merge_threshold": ("memory_optimizer_auto_merge_threshold", float),
            "memory_optimizer_split_min_chars": ("memory_optimizer_split_min_chars", int),
            "telegram_token": "telegram_token",
            "telegram_voice_reply": ("telegram_voice_reply", lambda v: v.lower() in ("1", "true", "yes")),
            "telegram_language": "telegram_language",
            "telegram_allowed_ids": "telegram_allowed_ids",
        }
        for key, val in data.items():
            if key in ini_map:
                attr = ini_map[key]
                if isinstance(attr, tuple):
                    name, cast = attr
                    try:
                        setattr(self, name, cast(val))
                    except (ValueError, TypeError):
                        pass
                else:
                    setattr(self, attr, val)

    def _apply_dict(self, overrides: dict):
        for k, v in overrides.items():
            if hasattr(self, k):
                setattr(self, k, v)
