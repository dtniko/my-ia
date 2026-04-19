"""CLI — helpers colori e output terminale."""
from __future__ import annotations
import sys


class CLI:
    # ANSI colors
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    _colors_enabled = sys.stdout.isatty()

    @classmethod
    def _c(cls, code: str, text: str) -> str:
        if not cls._colors_enabled:
            return text
        return f"{code}{text}{cls.RESET}"

    @classmethod
    def cyan(cls, t): return cls._c(cls.CYAN, t)
    @classmethod
    def green(cls, t): return cls._c(cls.GREEN, t)
    @classmethod
    def yellow(cls, t): return cls._c(cls.YELLOW, t)
    @classmethod
    def red(cls, t): return cls._c(cls.RED, t)
    @classmethod
    def bold(cls, t): return cls._c(cls.BOLD, t)
    @classmethod
    def dim(cls, t): return cls._c(cls.DIM, t)
    @classmethod
    def magenta(cls, t): return cls._c(cls.MAGENTA, t)

    @classmethod
    def header(cls, text: str):
        print(f"\n{cls.bold(cls.cyan('═══ ' + text + ' ═══'))}\n")

    @classmethod
    def step(cls, text: str):
        print(f"{cls.cyan('→')} {text}")

    @classmethod
    def info(cls, text: str):
        print(f"{cls.dim('ℹ')} {text}")

    @classmethod
    def success(cls, text: str):
        print(f"{cls.green('✓')} {text}")

    @classmethod
    def warning(cls, text: str):
        print(f"{cls.yellow('⚠')} {text}")

    @classmethod
    def error(cls, text: str):
        print(f"{cls.red('✗')} {text}", file=sys.stderr)

    @classmethod
    def tool(cls, name: str, args: dict = None):
        args_str = ""
        if args:
            key_parts = [f"{k}={repr(v)[:30]}" for k, v in list(args.items())[:3]]
            args_str = f"({', '.join(key_parts)})"
        print(f"  {cls.magenta('⚙')} {cls.bold(name)}{cls.dim(args_str)}")

    @classmethod
    def tool_result(cls, result: str, max_len: int = 120):
        preview = result[:max_len].replace("\n", " ")
        if len(result) > max_len:
            preview += "..."
        print(f"  {cls.dim('→')} {cls.dim(preview)}")

    @classmethod
    def banner(cls):
        print(cls.bold(cls.cyan("""
 ██╗  ████████╗███████╗██╗ █████╗     ██████╗ ██╗   ██╗
 ██║  ╚══██╔══╝██╔════╝██║██╔══██╗    ██╔══██╗╚██╗ ██╔╝
 ██║     ██║   ███████╗██║███████║    ██████╔╝ ╚████╔╝
 ██║     ██║   ╚════██║██║██╔══██║    ██╔═══╝   ╚██╔╝
 ███████╗██║   ███████║██║██║  ██║    ██║        ██║
 ╚══════╝╚═╝   ╚══════╝╚═╝╚═╝  ╚═╝    ╚═╝        ╚═╝
""")))
        print(cls.dim(" Local Thinking Software Intelligence Agent — Python Edition\n"))
