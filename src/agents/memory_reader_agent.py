"""
MemoryReaderAgent — estrae fatti strutturati dalle conversazioni (stile ASMR).

Tre "reader" concettuali girano in parallelo sullo stesso testo con prompt diversi:
  1. personal_reader  — info personali, identità, ruoli, contatti
  2. preference_reader — preferenze, stili, scelte ricorrenti
  3. event_reader      — eventi, decisioni, date, scadenze

Il merge produce una lista di fatti strutturati con categorizzazione
MemPalace (wing/hall/room). Ogni fatto viene poi scritto nella memoria
appropriata dal MemoryOrchestratorAgent.
"""
from __future__ import annotations
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from typing import Optional

from src.http.llm_client import LlmClientInterface


READER_PROMPTS = {
    "personal": """Sei un reader specializzato in INFO PERSONALI.
Estrai dal testo SOLO fatti su persone, identità, ruoli, email, telefoni,
relazioni professionali.
Se il testo non contiene nessun fatto di questo tipo, rispondi con [].

Formato output: JSON array. Ogni item:
{"wing": "utente|persona:<nome>", "hall": "personal", "room": "<argomento>",
 "content": "<fatto dichiarativo in una frase>"}""",

    "preference": """Sei un reader specializzato in PREFERENZE e stili.
Estrai dal testo SOLO preferenze esplicite o implicite: linguaggi, tool,
stile di codice, formati preferiti, abitudini.
Se il testo non contiene preferenze, rispondi con [].

Formato output: JSON array. Ogni item:
{"wing": "utente|progetto:<nome>", "hall": "preferences", "room": "<argomento>",
 "content": "<preferenza in una frase>"}""",

    "event": """Sei un reader specializzato in EVENTI e DECISIONI.
Estrai dal testo SOLO eventi, decisioni, date, scadenze, obiettivi concreti.
Se il testo non contiene eventi, rispondi con [].

Formato output: JSON array. Ogni item:
{"wing": "progetto:<nome>|utente", "hall": "events|decisions",
 "room": "<argomento>", "content": "<evento o decisione in una frase>",
 "date": "<ISO 8601 se presente>"}""",

    "directive": """Sei un reader specializzato in ISTRUZIONI ALL'ASSISTENTE.
Estrai SOLO istruzioni che l'utente dà direttamente all'assistente su come
deve comportarsi, chiamarsi, rispondere. Esempi di trigger:
  - "ricordati che ti chiami ..."
  - "da oggi ti chiami ..."
  - "sei il mio assistente ..."
  - "chiamami sempre ..."
  - "rispondi sempre in ..."
  - "non fare mai ..."
Se il testo non contiene nessuna istruzione di questo tipo, rispondi con [].

Formato output: JSON array. Ogni item:
{"wing": "agente", "hall": "directives", "room": "<categoria breve>",
 "content": "<istruzione in imperativo, es. 'Chiamati Daniela'>",
 "permanent": true}""",
}


class MemoryReaderAgent:
    def __init__(self, client: LlmClientInterface, model: str, timeout: int = 30):
        self.client = client
        self.model = model
        self.timeout = timeout

    def read(self, text: str, categories: Optional[list[str]] = None) -> list[dict]:
        """Estrae fatti dal testo invocando i reader in parallelo."""
        if not text.strip():
            return []
        cats = categories or list(READER_PROMPTS.keys())

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=len(cats)) as ex:
            futures = {ex.submit(self._run_reader, cat, text): cat for cat in cats}
            for fut in futures:
                try:
                    facts = fut.result(timeout=self.timeout)
                    for f in facts:
                        f.setdefault("reader", futures[fut])
                        results.append(f)
                except (FutTimeoutError, Exception):
                    continue

        return _dedupe(results)

    def _run_reader(self, category: str, text: str) -> list[dict]:
        prompt = READER_PROMPTS[category]
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]
        try:
            resp = self.client.chat(model=self.model, messages=messages)
        except Exception:
            return []
        content = resp.get("message", {}).get("content", "") or ""
        return _parse_json_array(content)


def _parse_json_array(text: str) -> list[dict]:
    """Estrae il primo JSON array dal testo (robusto a preamboli LLM)."""
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("wing"), it.get("hall"), it.get("room"), (it.get("content") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
