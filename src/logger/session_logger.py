"""SessionLogger — JSONL operazionale + Markdown summary."""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime
from pathlib import Path


class SessionLogger:
    def __init__(self, mode: str = "interactive", task: str = "", work_dir: str = ""):
        self.session_id = str(uuid.uuid4())[:8]
        self.mode = mode
        self.task = task
        self.work_dir = work_dir
        self.start_time = datetime.now()

        base = Path.home() / ".ltsia"
        self.log_dir = base / "logs"
        self.session_dir = base / "sessions"
        self.error_dir = base / "errors"
        for d in [self.log_dir, self.session_dir, self.error_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._events: list[dict] = []
        self._files_created: list[str] = []
        self._errors: list[str] = []
        self._total_tokens: int = 0
        self._test_result: dict = {}
        self._iterations: int = 0

    def tool_call(self, tool_name: str, args: dict, result: str):
        self._events.append({
            "type": "tool_call",
            "tool": tool_name,
            "args_keys": list(args.keys()) if args else [],
            "result_len": len(result),
            "ts": datetime.now().isoformat(),
        })

    def iteration(self, n: int):
        self._iterations = n

    def file_created(self, path: str):
        self._files_created.append(path)

    def error(self, msg: str):
        self._errors.append(msg)
        err_file = self.error_dir / f"{self.start_time.strftime('%Y-%m-%d')}.log"
        with open(err_file, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] [{self.session_id}] {msg}\n")

    def test_result(self, success: bool, output: str):
        self._test_result = {"success": success, "output": output[:500]}

    def add_tokens(self, n: int):
        self._total_tokens += n

    def flush(self):
        """Scrivi JSONL e Markdown summary."""
        data = {
            "session_id": self.session_id,
            "mode": self.mode,
            "task": self.task,
            "work_dir": self.work_dir,
            "start": self.start_time.isoformat(),
            "end": datetime.now().isoformat(),
            "iterations": self._iterations,
            "files_created": self._files_created,
            "total_tokens": self._total_tokens,
            "test_result": self._test_result,
            "errors": self._errors,
            "events": self._events,
        }

        # JSONL
        jsonl_file = self.log_dir / f"{self.start_time.strftime('%Y-%m-%d')}.jsonl"
        with open(jsonl_file, "a") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        # Markdown summary
        md_name = f"{self.start_time.strftime('%Y-%m-%d_%H-%M-%S')}_{self.session_id}.md"
        md_file = self.session_dir / md_name
        with open(md_file, "w") as f:
            f.write(self._generate_markdown(data))

    def _generate_markdown(self, data: dict) -> str:
        lines = [
            f"# Sessione {data['session_id']}",
            f"**Data**: {data['start']}",
            f"**Modalità**: {data['mode']}",
            f"**Task**: {data['task'] or '—'}",
            f"**Work Dir**: {data['work_dir']}",
            "",
            f"## Statistiche",
            f"- Iterazioni: {data['iterations']}",
            f"- Token totali: {data['total_tokens']}",
            f"- File creati: {len(data['files_created'])}",
            f"- Errori: {len(data['errors'])}",
            "",
        ]
        if data["files_created"]:
            lines.append("## File creati")
            for f in data["files_created"]:
                lines.append(f"- `{f}`")
            lines.append("")
        if data["test_result"]:
            tr = data["test_result"]
            lines.append(f"## Test: {'✓' if tr.get('success') else '✗'}")
            lines.append(f"```\n{tr.get('output', '')}\n```")
        return "\n".join(lines)
