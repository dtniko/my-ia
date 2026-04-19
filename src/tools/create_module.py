"""
CreateModuleTool — permette all'LLM di creare un nuovo tool o agente Python
e caricarlo IMMEDIATAMENTE nel sistema senza riavvio.

Questo è il meccanismo di auto-estensibilità runtime di LTSIA-py.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import TYPE_CHECKING
from .base_tool import BaseTool

if TYPE_CHECKING:
    from .tool_registry import ToolRegistry


class CreateModuleTool(BaseTool):
    """
    Pseudo-tool iniettato da Application dopo che ToolRegistry è creato,
    così ha accesso al registry per il hot-reload.
    """

    def __init__(self, registry: "ToolRegistry", extensions_dir: str):
        self.registry = registry
        self.extensions_dir = extensions_dir
        os.makedirs(extensions_dir, exist_ok=True)

    def get_name(self) -> str:
        return "create_module"

    def get_description(self) -> str:
        return (
            "Crea un nuovo tool o agente Python e lo carica IMMEDIATAMENTE nel sistema senza riavvio. "
            "Il codice deve definire una o più classi che estendono BaseTool "
            "(from src.tools.base_tool import BaseTool). "
            "Dopo la creazione, il tool sarà disponibile nel prossimo tool_call. "
            "Usa questo per estendere le capacità del sistema a runtime."
        )

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "module_name": {
                    "type": "string",
                    "description": "Nome del modulo (es. 'weather_tool', 'git_tool'). Diventa il nome del file .py",
                },
                "code": {
                    "type": "string",
                    "description": (
                        "Codice Python completo. Deve contenere almeno una classe che estende BaseTool. "
                        "Esempio:\n"
                        "from src.tools.base_tool import BaseTool\n\n"
                        "class WeatherTool(BaseTool):\n"
                        "    def get_name(self): return 'get_weather'\n"
                        "    def get_description(self): return 'Get weather'\n"
                        "    def get_parameters(self): return {'type':'object','properties':{'city':{'type':'string'}},'required':['city']}\n"
                        "    def execute(self, args): return f\"Weather in {args['city']}: sunny\""
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Descrizione del modulo (opzionale, per logging)",
                },
            },
            "required": ["module_name", "code"],
        }

    def execute(self, args: dict) -> str:
        module_name = args.get("module_name", "").strip()
        code = args.get("code", "")
        description = args.get("description", "")

        if not module_name:
            return "ERROR: module_name obbligatorio"
        if not code:
            return "ERROR: code obbligatorio"

        # Sanifica nome
        module_name = module_name.replace("-", "_").replace(" ", "_")
        if not module_name.endswith(".py"):
            module_name += ".py"

        file_path = os.path.join(self.extensions_dir, module_name)

        # Scrivi il file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return f"ERROR: impossibile scrivere file: {e}"

        # Carica immediatamente
        try:
            loaded = self.registry.load_module_from_file(file_path)
        except Exception as e:
            # Elimina il file se il caricamento fallisce
            try:
                os.remove(file_path)
            except Exception:
                pass
            return f"ERROR: codice scritto ma caricamento fallito: {e}"

        if not loaded:
            return (
                f"File scritto in {file_path} ma nessun BaseTool trovato. "
                f"Assicurati che le classi estendano BaseTool e non siano astratte."
            )

        desc_str = f" — {description}" if description else ""
        return (
            f"Modulo caricato con successo{desc_str}\n"
            f"File: {file_path}\n"
            f"Tool registrati: {', '.join(loaded)}\n"
            f"Puoi usare questi tool immediatamente nel prossimo messaggio."
        )


class ReloadModuleTool(BaseTool):
    """Ricarica un modulo esistente dal disco (es. dopo modifiche manuali)."""

    def __init__(self, registry: "ToolRegistry", extensions_dir: str):
        self.registry = registry
        self.extensions_dir = extensions_dir

    def get_name(self) -> str:
        return "reload_module"

    def get_description(self) -> str:
        return "Ricarica un modulo Python già esistente nella directory extensions/. Utile dopo modifiche manuali."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Nome del file .py (es. 'weather_tool.py')"},
            },
            "required": ["module_name"],
        }

    def execute(self, args: dict) -> str:
        name = args.get("module_name", "").strip()
        if not name.endswith(".py"):
            name += ".py"
        file_path = os.path.join(self.extensions_dir, name)
        if not os.path.exists(file_path):
            return f"ERROR: file non trovato: {file_path}"
        try:
            loaded = self.registry.load_module_from_file(file_path)
            return f"Ricaricato: {file_path}\nTool: {', '.join(loaded) if loaded else 'nessuno'}"
        except Exception as e:
            return f"ERROR: {e}"


class ListModulesTool(BaseTool):
    """Elenca i moduli dinamici caricati e i tool disponibili."""

    def __init__(self, registry: "ToolRegistry", extensions_dir: str):
        self.registry = registry
        self.extensions_dir = extensions_dir

    def get_name(self) -> str:
        return "list_modules"

    def get_description(self) -> str:
        return "Elenca tutti i tool disponibili nel sistema (built-in + dinamici caricati a runtime)."

    def get_parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> str:
        import os, json
        tools = self.registry.list_tools()
        # Elenca file nella extensions dir
        ext_files = []
        try:
            for f in os.listdir(self.extensions_dir):
                if f.endswith(".py") and not f.startswith("_"):
                    ext_files.append(f)
        except Exception:
            pass
        result = {
            "total_tools": len(tools),
            "tools": sorted(tools),
            "dynamic_modules": sorted(ext_files),
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
