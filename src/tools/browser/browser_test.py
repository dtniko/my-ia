"""BrowserTestTool — esegue test Playwright tramite Node.js."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from ..base_tool import BaseTool


class BrowserTestTool(BaseTool):
    def __init__(self, work_dir: str):
        self.work_dir = work_dir

    def get_name(self): return "browser_test"
    def get_description(self): return "Testa una web app con Playwright. Esegui azioni e assertion sul browser."
    def get_parameters(self):
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL da testare"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": [
                                "click", "type", "select", "check", "clear",
                                "wait_for", "focus", "blur", "assert_text",
                                "assert_no_text", "assert_element", "assert_title",
                            ]},
                            "selector": {"type": "string"},
                            "value": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                    "description": "Passi da eseguire",
                },
            },
            "required": ["url"],
        }

    def execute(self, args: dict) -> str:
        url = args.get("url", "")
        steps = args.get("steps", [])
        if not url:
            return "ERROR: url obbligatorio"

        # Genera script Playwright
        script = self._generate_script(url, steps)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            node_modules = os.path.expanduser("~/.ltsia/node_modules")
            env = os.environ.copy()
            env["NODE_PATH"] = node_modules
            r = subprocess.run(
                ["node", script_path],
                capture_output=True, text=True, timeout=60, env=env,
            )
            output = r.stdout + r.stderr
            return self.truncate(output)
        except FileNotFoundError:
            return "ERROR: Node.js non trovato. Installa Node.js per i test browser."
        except subprocess.TimeoutExpired:
            return "ERROR: timeout test browser"
        except Exception as e:
            return f"ERROR: {e}"
        finally:
            try:
                os.remove(script_path)
            except Exception:
                pass

    def _generate_script(self, url: str, steps: list) -> str:
        steps_js = json.dumps(steps)
        return f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch({{
    executablePath: process.env.CHROME_PATH || undefined,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  }});
  const page = await browser.newPage();
  try {{
    await page.goto({json.dumps(url)}, {{ waitUntil: 'networkidle' }});
    const steps = {steps_js};
    for (const step of steps) {{
      if (step.action === 'click') await page.click(step.selector);
      else if (step.action === 'type') await page.fill(step.selector, step.value || '');
      else if (step.action === 'assert_text') {{
        const text = await page.textContent('body');
        if (!text.includes(step.text)) throw new Error('Text not found: ' + step.text);
      }}
      else if (step.action === 'assert_title') {{
        const title = await page.title();
        if (!title.includes(step.text)) throw new Error('Title mismatch: ' + title);
      }}
      else if (step.action === 'wait_for') await page.waitForSelector(step.selector, {{ timeout: 5000 }});
    }}
    console.log('TESTS PASSED');
  }} catch(e) {{
    console.error('TEST FAILED:', e.message);
    process.exit(1);
  }} finally {{
    await browser.close();
  }}
}})();
"""
