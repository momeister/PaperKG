from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


def _python_executable() -> str:
    if sys.platform.startswith("win"):
        local_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        local_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if local_python.exists():
        return str(local_python)
    return sys.executable or "python"


def _npm_executable() -> str:
    return "npm.cmd" if sys.platform.startswith("win") else "npm"


def _start(cmd: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=cwd)


def _wait_for_api(base_url: str, timeout_seconds: float = 20.0) -> bool:
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
    parser = argparse.ArgumentParser(description="Run ScienceKG product API and React frontend.")
    parser.add_argument("--api-only", action="store_true", help="Start only FastAPI.")
    parser.add_argument("--frontend-only", action="store_true", help="Start only Vite.")
    parser.add_argument("--api-port", type=int, default=8000, help="FastAPI port.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Vite port.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.api_only and args.frontend_only:
        raise SystemExit("Choose either --api-only or --frontend-only, not both.")

    python = _python_executable()
    api_url = f"http://127.0.0.1:{args.api_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    processes: list[subprocess.Popen] = []

    if not args.frontend_only:
        print(f"[product] Starting API on {api_url}")
        processes.append(
            _start(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "api.product_main:app",
                    "--reload",
                    "--port",
                    str(args.api_port),
                ],
                PROJECT_ROOT,
            )
        )
        if _wait_for_api(api_url):
            print(f"[product] API ready: {api_url}")
        else:
            print("[product] API did not become ready in time.")

    if not args.api_only:
        if not (FRONTEND_ROOT / "node_modules").exists():
            raise SystemExit("frontend/node_modules is missing. Run `npm.cmd install` in frontend/ first.")
        print(f"[product] Starting frontend on {frontend_url}")
        processes.append(
            _start(
                [
                    _npm_executable(),
                    "run",
                    "dev",
                    "--",
                    "--port",
                    str(args.frontend_port),
                ],
                FRONTEND_ROOT,
            )
        )

    if not processes:
        print("[product] Nothing to start.")
        return

    def _shutdown(*_args: object) -> None:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            if not any(proc.poll() is None for proc in processes):
                break
            time.sleep(0.5)
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
