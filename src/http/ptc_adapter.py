"""
PTCAdapter — Programmatic Tool Calling via prompt engineering.

Wrappa qualsiasi LlmClientInterface e implementa tool calling in modo
completamente model-agnostic: i tool vengono descritti nel system prompt
come testo, e le chiamate vengono parsate dal testo di risposta.

Vantaggi rispetto al native function calling:
  - Funziona con qualsiasi modello, anche senza supporto server-side tools
  - Risparmio token: gli schemi JSON non vengono inviati ad ogni chiamata API
  - I risultati tool intermedi vengono iniettati come testo, non come
    messaggi role:tool che molti modelli non gestiscono bene
  - Parsing resiliente: fallback su regex se il JSON è leggermente malformato

Formato tool call emesso dal modello:
    <tool_call>
    {"name": "nome_tool", "arguments": {"param": "valore"}}
    </tool_call>

Formato risultato iniettato nel contesto:
    <tool_result name="nome_tool">
    contenuto risultato
    </tool_result>
"""
from __future__ import annotations
import json
import re
from typing import Callable, Optional

from .llm_client import LlmClientInterface


# ── Costanti prompt ────────────────────────────────────────────────────────────

_TOOL_SECTION_HEADER = """
## Tool disponibili

Puoi eseguire azioni usando i tool elencati sotto. Per chiamare un tool usa ESATTAMENTE questo formato nel tuo messaggio:

<tool_call>
{"name": "NOME_TOOL", "arguments": {"param1": "valore1", "param2": "valore2"}}
</tool_call>

Regole:
- Usa un blocco <tool_call> per ogni tool che vuoi chiamare
- Attendi il risultato (<tool_result>) prima di fare la chiamata successiva
- I parametri "required" sono obbligatori
- Non inventare tool non elencati sotto
- Per APRIRE un'applicazione macOS (Spotify, Safari, Terminal, ecc.) usa SEMPRE macos_open_app, MAI read_file

### Tool disponibili:
"""

_TOOL_RESULT_TEMPLATE = '<tool_result name="{name}">\n{content}\n</tool_result>'

_TOOL_CALL_RE = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL)
_JSON_IN_TEXT_RE = re.compile(r'\{.*\}', re.DOTALL)


# ── PTCAdapter ─────────────────────────────────────────────────────────────────

class PTCAdapter(LlmClientInterface):
    """
    Adapter PTC trasparente: riceve/ritorna le stesse strutture di LlmClientInterface
    quindi BaseAgent e ContextManager non richiedono alcuna modifica.

    Parametri
    ---------
    inner : LlmClientInterface — client LLM sottostante (OpenAIClient, OllamaClient, …)
    """

    def __init__(self, inner: LlmClientInterface):
        self.inner = inner

    def ping(self) -> bool:
        return self.inner.ping()

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        on_stream: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        Se tools è fornito:
          1. Inietta la descrizione tool nel system prompt
          2. Converte messaggi role:tool in user messages testuali
          3. Chiama il modello SENZA il campo tools (niente native function calling)
          4. Parsa i blocchi <tool_call> dalla risposta e li ritorna nel formato standard
        """
        if tools:
            messages = _inject_tools_into_system(messages, tools)

        messages = _flatten_tool_messages(messages)

        result = self.inner.chat(model, messages, tools=None, on_stream=on_stream)

        if tools:
            result = _extract_tool_calls(result)

        return result


# ── Tool injection ─────────────────────────────────────────────────────────────

def _inject_tools_into_system(messages: list[dict], tools: list[dict]) -> list[dict]:
    """Aggiunge la sezione tool al system message (o crea un system message)."""
    tool_text = _TOOL_SECTION_HEADER + _format_tools_as_text(tools)
    msgs = list(messages)

    for i, m in enumerate(msgs):
        if m.get("role") == "system":
            existing = m.get("content", "")
            # Evita di duplicare se già iniettato (sessioni lunghe con compaction)
            if "## Tool disponibili" not in existing:
                msgs[i] = {**m, "content": existing + "\n" + tool_text}
            return msgs

    # Nessun system message — inserisci in testa
    msgs.insert(0, {"role": "system", "content": tool_text.strip()})
    return msgs


def _format_tools_as_text(tools: list[dict]) -> str:
    """Formatta la lista tool come testo leggibile per il modello."""
    lines: list[str] = []
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))

        lines.append(f"\n**{name}**")
        lines.append(f"Descrizione: {desc}")

        if props:
            lines.append("Parametri:")
            for pname, pinfo in props.items():
                req_tag = " [REQUIRED]" if pname in required else " [optional]"
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                enum_vals = pinfo.get("enum", [])
                enum_str = f" — valori: {enum_vals}" if enum_vals else ""
                lines.append(f"  • {pname} ({ptype}{req_tag}): {pdesc}{enum_str}")

    return "\n".join(lines)


# ── Message flattening ─────────────────────────────────────────────────────────

def _flatten_tool_messages(messages: list[dict]) -> list[dict]:
    """
    Converte la cronologia messaggi per modelli senza native function calling:
      - role:tool  → blocco <tool_result> in un user message
      - role:assistant con tool_calls → mantieni solo content (le tool call
        erano già nel testo; il campo tool_calls viene rimosso)
    Raggruppa più tool_result consecutivi in un singolo user message.
    """
    # Passo 1: costruisci mappa tool_call_id → nome tool
    id_to_name: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls", []):
                tc_id = tc.get("id", "")
                tc_name = tc.get("function", {}).get("name", "tool")
                if tc_id:
                    id_to_name[tc_id] = tc_name

    result: list[dict] = []
    pending: list[str] = []

    def flush():
        if pending:
            result.append({"role": "user", "content": "\n\n".join(pending)})
            pending.clear()

    for m in messages:
        role = m.get("role", "")

        if role == "tool":
            tc_id = m.get("tool_call_id", "")
            name = id_to_name.get(tc_id, "tool")
            content = str(m.get("content", ""))
            pending.append(_TOOL_RESULT_TEMPLATE.format(name=name, content=content))

        elif role == "assistant":
            flush()
            # Rimuovi tool_calls — in modalità PTC il modello non riceve questo campo
            clean = {k: v for k, v in m.items() if k != "tool_calls"}
            result.append(clean)

        else:
            flush()
            result.append(m)

    flush()
    return result


# ── Tool call extraction ───────────────────────────────────────────────────────

def _extract_tool_calls(result: dict) -> dict:
    """
    Parsa i blocchi <tool_call> dal content della risposta e li converte
    nel formato standard tool_calls usato da BaseAgent.
    """
    message = result.get("message", {})
    content = message.get("content", "") or ""

    tool_calls = _parse_tool_calls_from_text(content)
    if not tool_calls:
        return result

    # Pulisci il content rimuovendo i blocchi <tool_call>
    clean_content = _TOOL_CALL_RE.sub("", content).strip()

    new_message = {**message, "content": clean_content, "tool_calls": tool_calls}
    return {**result, "message": new_message}


def _parse_tool_calls_from_text(text: str) -> list[dict]:
    """Parsa tutti i blocchi <tool_call>...</tool_call> dal testo."""
    calls: list[dict] = []

    for i, match in enumerate(_TOOL_CALL_RE.finditer(text)):
        raw = match.group(1).strip()
        data = _try_parse_json(raw)
        if data is None:
            continue

        name = data.get("name", "")
        arguments = data.get("arguments", data.get("args", {}))

        if not name:
            continue

        calls.append({
            "id": f"ptc_{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })

    return calls


def _try_parse_json(text: str) -> Optional[dict]:
    """Prova a parsare JSON con fallback su estrazione regex."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: estrai il primo oggetto JSON trovato nel testo
    m = _JSON_IN_TEXT_RE.search(text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None
