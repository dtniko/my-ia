"""ExecutionAgent — genera UN file per volta, nessun tool call."""
from __future__ import annotations
import os
from src.http.llm_client import LlmClientInterface
from src.context.context_manager import ContextManager

SYSTEM_PROMPT = """Sei un esperto sviluppatore software. Il tuo compito è scrivere il contenuto COMPLETO di un singolo file.

REGOLE ASSOLUTE:
- Rispondi SOLO con il codice/contenuto del file — niente spiegazioni, niente markdown fence
- Il codice deve essere completo, funzionante e production-ready
- Non troncare, non usare placeholder come "// TODO" o "..."
- Se è un package.json, includi tutte le dipendenze necessarie
- Se è HTML con CSS, includi tutto inline se necessario

Rispondi SOLO con il contenuto del file."""


class ExecutionAgent:
    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        on_stream=None,
    ):
        self.client = client
        self.model = model
        self.on_stream = on_stream

    def create_file(
        self,
        path: str,
        description: str,
        context_info: str = "",
        work_dir: str = "/tmp",
    ) -> str:
        """Genera contenuto file e lo scrive su disco. Ritorna path o ERROR:..."""
        ext = os.path.splitext(path)[1].lower()
        lang_hint = self._get_language_hint(ext)

        user_prompt = f"Crea il file `{path}` ({lang_hint}).\n\nDescrizione: {description}"
        if context_info:
            user_prompt += f"\n\nContesto progetto:\n{context_info}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = self.client.chat(
                model=self.model,
                messages=messages,
                on_stream=self.on_stream,
            )
            content = result["message"].get("content", "")
            content = self._strip_code_fences(content)

            # Scrivi su disco
            full_path = path if os.path.isabs(path) else os.path.join(work_dir, path)
            os.makedirs(os.path.dirname(os.path.abspath(full_path)), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return full_path
        except Exception as e:
            return f"ERROR: {e}"

    def _get_language_hint(self, ext: str) -> str:
        hints = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".jsx": "React JSX", ".tsx": "React TSX", ".html": "HTML",
            ".css": "CSS", ".json": "JSON", ".md": "Markdown",
            ".sh": "Bash script", ".php": "PHP", ".go": "Go",
            ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
        }
        return hints.get(ext, "file di testo")

    def _strip_code_fences(self, content: str) -> str:
        """Rimuovi markdown code fences se presenti."""
        import re
        content = re.sub(r'^```[a-zA-Z]*\n', '', content.strip())
        content = re.sub(r'\n```$', '', content)
        return content.strip()
