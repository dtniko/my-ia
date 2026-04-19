# LTSIA-py — Local Thinking Software Intelligence Agent

Riscrittura Python di LTSIA con auto-estensibilità runtime. Agente AI locale che usa LLM remoti (Ollama + vLLM) per pianificare, scrivere codice ed eseguire comandi in autonomia.

## Avvio

```bash
python main.py                          # REPL interattivo
python main.py "Crea un app React"      # one-shot
python main.py --doctor                 # diagnostica
python main.py --work-dir=/tmp/sandbox  # override work dir
```

Flag disponibili: `--thinking-host`, `--thinking-port`, `--thinking-model`, `--exec-host`, `--exec-port`, `--exec-model`, `--context-window`, `--test-command` (solo one-shot).

## Architettura

### Entry point
- `main.py` — parse CLI, carica `Config`, istanzia `Application`
- `src/application.py` — orchestratore: inizializza client, tool registry, agenti, wira i delegate, gestisce modalità one-shot e REPL

### Config (`src/config.py`)
Cascata: `CLI flag > ./ltsia.ini > ~/.ltsia/config.ini > env var > default`

Env vars: `LTSIA_THINKING_HOST`, `LTSIA_THINKING_PORT`, `LTSIA_THINKING_MODEL`, `LTSIA_EXECUTION_HOST`, `LTSIA_EXECUTION_PORT`, `LTSIA_EXECUTION_MODEL`, `LTSIA_CONTEXT_WINDOW`, `LTSIA_WORK_DIR`

### LLM remoti
- **Thinking**: Ollama su `192.168.250.203:11434` — modello `qwen3-coder:30b`
- **Execution**: vLLM (API OpenAI-compatible) su `10.149.245.212:8807` — modello `qwen3-instruct`
- `src/http/ollama_client.py` — client per Ollama
- `src/http/openai_client.py` — client OpenAI-compatible
- `src/http/ptc_adapter.py` — adattatore che wrappa OpenAIClient

### Agenti (`src/agents/`)
| File | Ruolo |
|------|-------|
| `base_agent.py` | Base class |
| `chat_agent.py` | REPL principale, usa tool registry, gestisce context window |
| `context_agent.py` | Costruisce contesto iniziale di sessione da memoria permanente + semantica |
| `project_manager_agent.py` | Pianificazione task → subtask |
| `execution_agent.py` | Creazione file con streaming |
| `testing_agent.py` | Test web (npm test ecc.) con retry |
| `cli_test_agent.py` | Test progetti CLI/Python |
| `search_agent.py` | Ricerche web |

### Tool Registry (`src/tools/tool_registry.py`)
Registro centrale di tutti i tool disponibili all'agente. Supporta:
- Tool statici (registrati all'avvio)
- **Moduli dinamici** (auto-estensibilità): caricati da `~/.ltsia/extensions/` — tool `create_module`, `reload_module`, `list_modules`
- Delegate pattern: `planning_delegate`, `file_creation_delegate`, `testing_delegate`, `search_delegate`

### Tool disponibili
```
filesystem/  → create_directory, delete_file, glob_search, grep_search,
               list_directory, move_file, read_file, write_file
shell/       → execute_command, smart_install
web/         → web_fetch, web_search
macos/       → applescript, clipboard, list_apps, open_app, screenshot
memory_tools/→ forget, list_memories, remember, search_memory
browser/     → browser_test, dev_server_manager, start_dev_server, stop_dev_server
jobs_tools/  → cancel_job, list_jobs, schedule_job
```

### Memoria
- **Permanente** (`src/memory/permanent_memory.py`) — key-value persistente su disco
- **Semantica** (`src/memory/semantic_memory.py`) — vettoriale via embedding (opzionale, richiede `embedding_host` in config)
- Embedding: `nomic-embed-text` via Ollama, SQLite locale in `~/.ltsia/semantic_memory.db`

### Job Manager (`src/jobs/`)
Job asincroni persistenti salvati in `~/.ltsia/jobs/`. Componenti: `job.py`, `job_store.py`, `job_worker.py`, `job_manager.py`.

### UI
- `src/ui/cli.py` — helpers terminale (step, success, error, warning, header)
- `src/ui/interactive.py` — REPL interattivo

### Voice (`src/voice/`)
- `voice_server.py` — WebSocket server su `ws://localhost:8765`
- `tts.py` — TTS via `edge-tts` (voce default: `it-IT-IsabellaNeural`, rate `+20%`)
- Avviato come thread daemon nel REPL interattivo
- Frontend React in `voice/` (dev: `cd voice && npm run dev`)

## Directory runtime
```
~/.ltsia/
  config.ini       # config globale
  extensions/      # moduli dinamici caricati a runtime
  jobs/            # job persistenti
  logs/            # log sessioni
  sessions/        # sessioni salvate
  errors/          # error dump
  semantic_memory.db
```

## Configurazione locale (`ltsia.ini`)
```ini
[ltsia]
thinking_host  = 192.168.250.203
thinking_port  = 11434
thinking_model = qwen3-coder:30b
context_window = 32768

exec_host  = 10.149.245.212
exec_port  = 8807
exec_model = qwen3-instruct

tts_voice = it-IT-IsabellaNeural
tts_rate  = +20%

ollama_timeout = 1800
```

## Dipendenze
```
requests>=2.31.0
websockets>=12.0
```
Opzionali: `edge-tts` (TTS), embedding libs per semantic memory.

## Aggiungere un nuovo tool
1. Creare `src/tools/<categoria>/my_tool.py` con una classe che estende `BaseTool`
2. Registrarla in `ToolRegistry` (o usare `create_module` a runtime)
3. Il `ChatAgent` la vede automaticamente tramite il registry

## Note importanti
- Il progetto è **italiano** — docstring, commenti e messaggi UI sono in italiano
- `work_dir` default: `/tmp/sandbox` — i file generati finiscono lì
- Il `ChatAgent` usa `exec_model` (vLLM), **non** il modello thinking (Ollama)
- Il modello thinking (Ollama) è usato solo per embedding e operazioni specifiche
- `--doctor` verifica connettività LLM, tool caricati, memoria semantica
