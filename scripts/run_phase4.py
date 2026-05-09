from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_ENTRYPOINTS = {
    "chat": "ui/chat_interface.py",
    "paper": "ui/paper_detail.py",
    "projects": "ui/project_manager.py",
}


def _python_executable() -> str:
    return sys.executable or "python"


def _start(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT)


def _wait_for_api(base_url: str, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.5)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-command runner for ScienceKG Phase 4")
    parser.add_argument("--api-only", action="store_true", help="Start only FastAPI")
    parser.add_argument("--ui-only", action="store_true", help="Start only Streamlit UI")
    parser.add_argument("--api-port", type=int, default=8000, help="FastAPI port")
    parser.add_argument("--ui-port", type=int, default=8501, help="Streamlit port")
    parser.add_argument(
        "--ui",
        choices=sorted(UI_ENTRYPOINTS),
        default="chat",
        help="Phase 4 UI entry point",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.api_only and args.ui_only:
        raise SystemExit("Choose either --api-only or --ui-only, not both.")

    python = _python_executable()
    base_url = f"http://127.0.0.1:{args.api_port}"
    processes: list[subprocess.Popen] = []

    if not args.ui_only:
        print(f"[phase4] Starting API on {base_url}")
        processes.append(
            _start(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "api.phase4_main:app",
                    "--reload",
                    "--port",
                    str(args.api_port),
                ]
            )
        )
        if _wait_for_api(base_url):
            print(f"[phase4] API ready: {base_url}")
        else:
            print("[phase4] API did not become ready in time.")

    if not args.api_only:
        ui_path = UI_ENTRYPOINTS[args.ui]
        ui_url = f"http://localhost:{args.ui_port}"
        print(f"[phase4] Starting {args.ui} UI on {ui_url}")
        processes.append(
            _start(
                [
                    python,
                    "-m",
                    "streamlit",
                    "run",
                    ui_path,
                    "--server.port",
                    str(args.ui_port),
                ]
            )
        )

    if not processes:
        print("[phase4] Nothing to start.")
        return

    def _shutdown(*_args: object) -> None:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            alive = [proc for proc in processes if proc.poll() is None]
            if not alive:
                break
            time.sleep(0.5)
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
