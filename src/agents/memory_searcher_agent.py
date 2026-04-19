"""
MemorySearcherAgent — ricerca parallela stile ASMR sulla memoria tiered.

Strategia: 3 passate indipendenti sulla stessa query, eseguite in parallelo:
  1. direct   — hits diretti (keyword match su medium + top-k semantic su long)
  2. context  — contesto esteso (query riscritta dall'LLM, threshold più basso)
  3. recent   — focus sulle info aggiornate di recente

Al merge, lo score finale è la media pesata dei match, con bonus per i fatti
trovati da più searcher.

La short-term viene sempre cercata prima (è O(1) e serve come fast-path).
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from typing import Optional

from src.http.llm_client import LlmClientInterface


REWRITE_PROMPT = """Sei un assistente di ricerca. Dato il messaggio dell'utente,
produci 3 query alternative (più corte, più lunghe, con sinonimi) per cercare
nella memoria dell'assistente. Output: JSON array di 3 stringhe."""


class MemorySearcherAgent:
    def __init__(
        self,
        client: LlmClientInterface,
        model: str,
        short_term=None,
        medium_term=None,
        long_term=None,
        rewriter: bool = True,
        timeout: int = 20,
    ):
        self.client = client
        self.model = model
        self.short = short_term
        self.medium = medium_term
        self.long = long_term
        self.rewriter = rewriter
        self.timeout = timeout

    # ── Public API ────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        scope: Optional[str] = None,
        limit: int = 8,
    ) -> dict:
        """
        Ritorna:
            {
              "hits": [ ... ],
              "confidence": float,   # 0..1
              "by_tier": {"short":n, "medium":n, "long":n},
            }
        """
        if not query.strip():
            return {"hits": [], "confidence": 0.0, "by_tier": {}}

        short_hits = self._short_search(query, scope)
        alt_queries = self._rewrite(query) if self.rewriter else [query]

        medium_hits, long_hits = [], []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = []
            # Searcher DIRECT: query originale
            futs.append(("direct_m", ex.submit(self._medium_search, query, limit)))
            futs.append(("direct_l", ex.submit(self._long_search, query, limit, 0.3)))
            # Searcher CONTEXT: query alternative (prendi la prima, se presente)
            alt_q = alt_queries[0] if alt_queries else query
            futs.append(("context_m", ex.submit(self._medium_search, alt_q, limit)))
            futs.append(("context_l", ex.submit(self._long_search, alt_q, limit, 0.2)))

            for label, fut in futs:
                try:
                    res = fut.result(timeout=self.timeout)
                except (FutTimeoutError, Exception):
                    continue
                if label.endswith("_m"):
                    medium_hits.extend(res)
                else:
                    long_hits.extend(res)

        merged = self._merge(short_hits, medium_hits, long_hits, limit)
        confidence = self._confidence(merged)
        return {
            "hits": merged,
            "confidence": confidence,
            "by_tier": {
                "short": len(short_hits),
                "medium": len(medium_hits),
                "long": len(long_hits),
            },
        }

    # ── Searchers individuali ─────────────────────────────────────────────────

    def _short_search(self, query: str, scope: Optional[str]) -> list[dict]:
        if not self.short:
            return []
        if scope:
            try:
                sc = self.short.scope(scope)
                return sc.recall(query)
            except Exception:
                return []
        return self.short.recall_all(query)

    def _medium_search(self, query: str, limit: int) -> list[dict]:
        if not self.medium:
            return []
        try:
            return self.medium.recall(query, limit=limit)
        except Exception:
            return []

    def _long_search(self, query: str, limit: int, threshold: float) -> list[dict]:
        if not self.long or not getattr(self.long, "is_ready", lambda: True)():
            return []
        try:
            hits = self.long.search(query, limit=limit, threshold=threshold)
            for h in hits:
                h["tier"] = "long"
            return hits
        except Exception:
            return []

    # ── Query rewriter ────────────────────────────────────────────────────────

    def _rewrite(self, query: str) -> list[str]:
        try:
            resp = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": REWRITE_PROMPT},
                    {"role": "user", "content": query},
                ],
            )
            content = resp.get("message", {}).get("content", "") or ""
            try:
                arr = json.loads(content)
                if isinstance(arr, list):
                    return [str(x) for x in arr][:3]
            except json.JSONDecodeError:
                pass
        except Exception:
            pass
        return [query]

    # ── Merge + confidence ────────────────────────────────────────────────────

    def _merge(
        self,
        short: list[dict],
        medium: list[dict],
        long: list[dict],
        limit: int,
    ) -> list[dict]:
        # Dedup by (tier, content-prefix) e sommiamo score per hit trovati da più passate.
        buckets: dict[tuple, dict] = {}
        for h in short + medium + long:
            content = str(h.get("content", ""))[:120]
            tier = h.get("tier", "?")
            key = (tier, content)
            if key in buckets:
                buckets[key]["score"] = max(buckets[key].get("score", 0), h.get("score", 1.0))
                buckets[key]["multi_hit"] = True
            else:
                buckets[key] = dict(h)

        # Boost tier short > medium > long a parità di score (recency bias).
        def sort_key(h: dict) -> float:
            base = float(h.get("score", 0.5))
            tier = h.get("tier", "long")
            boost = {"short": 0.15, "medium": 0.05, "long": 0.0}.get(tier, 0.0)
            if h.get("multi_hit"):
                boost += 0.1
            return -(base + boost)

        ordered = sorted(buckets.values(), key=sort_key)
        return ordered[:limit]

    def _confidence(self, hits: list[dict]) -> float:
        if not hits:
            return 0.0
        top = hits[0].get("score", 0.0)
        # Media dei top 3 come stima di robustezza.
        top3 = [h.get("score", 0.0) for h in hits[:3]]
        avg = sum(top3) / len(top3)
        return max(0.0, min(1.0, 0.6 * top + 0.4 * avg))
