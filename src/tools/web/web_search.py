"""Web search multi-sorgente parallelo."""
from __future__ import annotations
import json
import re
import urllib.parse
import concurrent.futures
import requests
from ..base_tool import BaseTool


class WebSearchTool(BaseTool):
    def get_name(self) -> str:
        return "web_search"

    def get_description(self) -> str:
        return "Cerca nel web. Usa più motori in parallelo e restituisce risultati aggregati."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query di ricerca"},
                "max_results": {"type": "integer", "description": "Risultati massimi (default 10)"},
            },
            "required": ["query"],
        }

    def execute(self, args: dict) -> str:
        query = args.get("query", "")
        max_results = int(args.get("max_results", 10))
        if not query:
            return "ERROR: query obbligatoria"

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(self._search_ddg, query): "ddg",
                ex.submit(self._search_wiki, query): "wiki",
            }
            all_results: list[dict] = []
            for future in concurrent.futures.as_completed(futures, timeout=15):
                try:
                    results = future.result()
                    all_results.extend(results)
                except Exception:
                    pass

        # De-dup per URL
        seen_urls = set()
        unique = []
        for r in all_results:
            url = r.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append(r)

        unique = unique[:max_results]
        if not unique:
            return "Nessun risultato trovato."

        lines = []
        for i, r in enumerate(unique, 1):
            lines.append(f"{i}. **{r.get('title', 'N/A')}**")
            lines.append(f"   URL: {r.get('url', '')}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")

        return "\n".join(lines)

    def _search_ddg(self, query: str) -> list[dict]:
        """DuckDuckGo instant answers."""
        try:
            url = "https://html.duckduckgo.com/html/"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.post(url, data={"q": query}, headers=headers, timeout=10)
            r.raise_for_status()
            results = []
            # Parsa risultati semplici
            pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            for m in pattern.finditer(r.text):
                href = m.group(1)
                title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                snippet = re.sub(r'<[^>]+>', '', m.group(3)).strip()
                # Decode DDG redirect
                if "uddg=" in href:
                    href = urllib.parse.unquote(re.search(r'uddg=([^&]+)', href).group(1))
                results.append({"url": href, "title": title, "snippet": snippet, "source": "ddg"})
                if len(results) >= 8:
                    break
            return results
        except Exception:
            return []

    def _search_wiki(self, query: str) -> list[dict]:
        """Wikipedia API search."""
        try:
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 3,
            }
            r = requests.get("https://en.wikipedia.org/w/api.php", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            results = []
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
                url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                results.append({"url": url, "title": title, "snippet": snippet, "source": "wiki"})
            return results
        except Exception:
            return []
