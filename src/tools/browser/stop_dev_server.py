from ..base_tool import BaseTool
from .dev_server_manager import DevServerManager


class StopDevServerTool(BaseTool):
    def get_name(self): return "stop_dev_server"
    def get_description(self): return "Ferma un dev server in background."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {"server_id": {"type": "string"}},
            "required": ["server_id"],
        }

    def execute(self, args: dict) -> str:
        sid = args.get("server_id", "")
        if not sid:
            return "ERROR: server_id obbligatorio"
        ok = DevServerManager.stop(sid)
        return f"Server {sid} fermato" if ok else f"ERROR: server {sid} non trovato"
