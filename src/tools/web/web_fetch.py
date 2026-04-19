"""Fetch URL e restituisce contenuto testo/markdown."""
from __future__ import annotations
import re
import requests
from ..base_tool import BaseTool


class WebFetchTool(BaseTool):
    def get_name(self) -> str:
        return "web_fetch"

    def get_description(self) -> str:
        return "Recupera il contenuto di un URL. Restituisce testo/markdown."

    def get_parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL da recuperare"},
                "max_chars": {"type": "integer", "description": "Caratteri massimi (default 16000)"},
            },
            "required": ["url"],
        }

    def execute(self, args: dict) -> str:
        url = args.get("url", "")
        max_chars = int(args.get("max_chars", 16000))
        if not url:
            return "ERROR: url obbligatorio"
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; LTSIA/1.0)"}
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            text = r.text
            if "html" in content_type:
                text = self._html_to_text(text)
            return self.truncate(text, max_chars)
        except Exception as e:
            return f"ERROR: {e}"

    def _html_to_text(self, html: str) -> str:
        # Rimuovi script/style
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Converti header
        for i in range(6, 0, -1):
            html = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', r'\n' + '#' * i + r' \1\n', html, flags=re.DOTALL | re.IGNORECASE)
        # Paragrafi e br
        html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<p[^>]*>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'</p>', '\n', html, flags=re.IGNORECASE)
        # Link
        html = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
        # Rimuovi tutti i tag
        html = re.sub(r'<[^>]+>', '', html)
        # Entità HTML base
        html = html.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
        # Normalizza spazi
        html = re.sub(r'\n{3,}', '\n\n', html)
        html = re.sub(r' +', ' ', html)
        return html.strip()
