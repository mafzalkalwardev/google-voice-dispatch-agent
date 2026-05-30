from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from src.paths import ensure_runtime_dirs, resource_path, runtime_base


APP_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _copy_runtime_examples() -> None:
    base = ensure_runtime_dirs()
    for name in (".env.example", "dialer_config.example.json", "README.md"):
        src = resource_path(name)
        dest = base / name
        if src.exists() and not dest.exists():
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass


def _console_port(args: list[str]) -> int:
    raw_port = os.environ.get("INDUS_CONSOLE_PORT")
    for idx, arg in enumerate(args):
        if arg == "--port" and idx + 1 < len(args):
            raw_port = args[idx + 1]
        elif arg.startswith("--port="):
            raw_port = arg.split("=", 1)[1]

    if not raw_port:
        return DEFAULT_PORT

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid console port: {raw_port}") from exc

    if port < 1 or port > 65535:
        raise SystemExit(f"Console port out of range: {port}")
    return port


def _open_browser_later(url: str) -> None:
    time.sleep(2.5)
    webbrowser.open(url)


def run_agent_cli(args: list[str]) -> None:
    base = ensure_runtime_dirs()
    os.chdir(base)
    sys.argv = ["src.main", *args]
    from src.main import main

    main()


def run_console(open_browser: bool = True, port: int = DEFAULT_PORT) -> None:
    base = ensure_runtime_dirs()
    _copy_runtime_examples()
    os.chdir(base)
    url = f"http://{APP_HOST}:{port}/run"

    if open_browser:
        threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()

    import uvicorn
    from src.web_app import app

    uvicorn.run(
        app,
        host=APP_HOST,
        port=port,
        reload=False,
        log_level="info",
        log_config=None,
        access_log=False,
    )


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--agent-cli":
        run_agent_cli(args[1:])
        return

    port = _console_port(args)

    # Ensure the console port is available (force-kill previous runs).
    try:
        from src.scripts.kill_port import kill_port  # type: ignore
        kill_port(port)
    except Exception:
        pass

    if "--no-browser" in args:
        run_console(open_browser=False, port=port)
        return
    run_console(open_browser=True, port=port)



if __name__ == "__main__":
    try:
        main()
    except BaseException:
        try:
            log_dir = ensure_runtime_dirs() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "desktop_app_error.log").write_text(
                traceback.format_exc(),
                encoding="utf-8",
            )
        finally:
            raise
