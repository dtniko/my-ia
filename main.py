#!/usr/bin/env python3
"""
LTSIA-py — Local Thinking Software Intelligence Agent
Riscrittura Python con auto-estensibilità runtime.

Uso:
  python main.py                          # REPL interattivo
  python main.py "Crea un app React"      # one-shot
  python main.py --doctor                 # diagnostica
  python main.py --work-dir=/tmp/sandbox  # imposta work dir

Flags:
  --work-dir=PATH
  --thinking-host=HOST
  --thinking-port=PORT
  --thinking-model=MODEL
  --exec-host=HOST
  --exec-port=PORT
  --exec-model=MODEL
  --test-command=CMD     (solo one-shot)
  --doctor
"""
from __future__ import annotations
import sys
import os

# Aggiungi root al path Python
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def parse_args(argv: list[str]) -> tuple[list[str], dict]:
    """Two-pass parser: separa flag --key=val dai positional arg."""
    flags = {}
    positional = []
    for arg in argv[1:]:
        if arg.startswith("--"):
            if "=" in arg:
                key, val = arg[2:].split("=", 1)
            else:
                key, val = arg[2:], "true"
            flags[key.replace("-", "_")] = val
        else:
            positional.append(arg)
    return positional, flags


def main():
    positional, flags = parse_args(sys.argv)

    # Importa dopo path setup
    from src.config import Config
    from src.ui.cli import CLI

    # Mappa flag CLI → config keys
    cli_overrides = {}
    flag_map = {
        "work_dir": "work_dir",
        "thinking_host": "thinking_host",
        "thinking_port": "thinking_port",
        "thinking_model": "thinking_model",
        "exec_host": "exec_host",
        "exec_port": "exec_port",
        "exec_model": "exec_model",
        "context_window": "context_window",
    }
    for flag_key, config_key in flag_map.items():
        if flag_key in flags:
            cli_overrides[config_key] = flags[flag_key]

    # Carica config
    config = Config.load(cli_overrides)

    # Modalità doctor
    if "doctor" in flags:
        from src.application import Application
        app = Application(config)
        app.doctor()
        return

    # Modalità one-shot o interattiva
    task = " ".join(positional) if positional else ""
    test_command = flags.get("test_command", "")

    from src.application import Application

    try:
        app = Application(config)
    except Exception as e:
        CLI.error(f"Errore inizializzazione: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if task:
        # One-shot
        exit_code = app.run(task, test_command)
        sys.exit(exit_code)
    else:
        # REPL interattivo
        app.interactive()


if __name__ == "__main__":
    main()
