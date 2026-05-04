from __future__ import annotations

import argparse
import httpx
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _start(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT)


def _python_executable() -> str:
    if sys.executable:
        return sys.executable
    return "python"


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


def _trigger_build(base_url: str) -> None:
    try:
        response = httpx.post(f"{base_url}/graph/phase2/build", json={}, timeout=30.0)
        if response.status_code == 200:
            print(f"[phase2] Build finished: {response.json()}")
        else:
            print(f"[phase2] Build failed ({response.status_code}): {response.text}")
    except Exception as exc:
        print(f"[phase2] Build request failed: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-command runner for ScienceKG Phase 2")
    parser.add_argument("--skip-build", action="store_true", help="Skip graph build step")
    parser.add_argument("--api-only", action="store_true", help="Start only FastAPI")
    parser.add_argument("--ui-only", action="store_true", help="Start only Streamlit UI")
    parser.add_argument("--api-port", type=int, default=8000, help="FastAPI port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.api_only and args.ui_only:
        raise SystemExit("Choose either --api-only or --ui-only, not both.")

    python = _python_executable()

    processes: list[subprocess.Popen] = []
    base_url = f"http://127.0.0.1:{args.api_port}"

    if not args.ui_only:
        print(f"[phase2] Starting API on {base_url}")
        processes.append(
            _start([
                python,
                "-m",
                "uvicorn",
                "api.main:app",
                "--reload",
                "--port",
                str(args.api_port),
            ])
        )

        if not args.skip_build:
            print("[phase2] Waiting for API health check...")
            if _wait_for_api(base_url):
                print("[phase2] Triggering graph build...")
                _trigger_build(base_url)
            else:
                print("[phase2] API did not become ready in time; skipping build step.")
    elif not args.skip_build:
        print("[phase2] Build step skipped because --ui-only is set.")

    if not args.api_only:
        print("[phase2] Starting Streamlit UI on http://localhost:8501")
        processes.append(
            _start([
                python,
                "-m",
                "streamlit",
                "run",
                "ui/graph_visualization.py",
            ])
        )

    if not processes:
        print("[phase2] Nothing to start.")
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
