"""Convenience runner for the FastAPI console.

- Forces console to a requested port (default 8000)
- Force-kills any process currently listening on that port (Windows)

Usage:
  python -m src.scripts.run_console_port --port 8000 --no-browser
"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        from src.scripts.kill_port import kill_port  # type: ignore

        kill_port(args.port)
    except Exception:
        pass

    from src.desktop_app import run_console  # type: ignore

    run_console(open_browser=not args.no_browser, port=args.port)


if __name__ == "__main__":
    main()

