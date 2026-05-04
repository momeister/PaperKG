#!/usr/bin/env python3
"""
ScienceKG Phase 3 One-Command Runner

Starts Phase 3 infrastructure:
- FastAPI API server for extraction endpoints
- Streamlit extraction UI dashboard
- Configurable LLM providers (Ollama, LM Studio, OpenAI, etc.)

Usage:
    python scripts/run_phase3.py                    # Start API + UI
    python scripts/run_phase3.py --api-only         # API only
    python scripts/run_phase3.py --ui-only          # UI only
    python scripts/run_phase3.py --api-port 9000    # Custom API port

Requires:
    - Running Ollama instance (or configured LLM provider)
    - config.yaml with LLM provider configuration
    - Phase 2 database at data/metadata.duckdb
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


def _wait_for_api(api_url: str, max_wait: int = 15) -> bool:
    """
    Poll API health endpoint until ready.

    Args:
        api_url: API base URL
        max_wait: Maximum seconds to wait

    Returns:
        True if API ready, False if timeout
    """
    for attempt in range(max_wait):
        try:
            response = httpx.get(f"{api_url}/health", timeout=2.0)
            if response.status_code == 200:
                print(f"✓ API ready at {api_url}")
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass

        print(f"  Waiting for API... ({attempt + 1}/{max_wait}s)")
        time.sleep(1)

    return False


def _trigger_extraction_demo(api_url: str) -> None:
    """
    Trigger extraction demo on ready API.

    Args:
        api_url: API base URL
    """
    demo_payload = {
        "paper_id": "arxiv_2024_demo",
        "text": """
            This paper introduces a novel approach to neural network optimization.
            We propose using adaptive learning rates with momentum-based updates.
            Key concepts: gradient descent, optimization, neural networks.
            The method achieves 95% accuracy on benchmark datasets.
        """.strip(),
        "provider": "ollama",
    }

    try:
        response = httpx.post(
            f"{api_url}/extraction/extract",
            json=demo_payload,
            timeout=10.0,
        )

        if response.status_code == 200:
            data = response.json()
            concepts_count = len(data.get("concepts", []))
            print(f"✓ Demo extraction successful ({concepts_count} concepts extracted)")
        else:
            print(f"  Demo extraction responded: {response.status_code}")

    except Exception as exc:
        print(f"  Demo extraction skipped: {exc}")


def _start(args: argparse.Namespace) -> None:
    """
    Start Phase 3 services.

    Args:
        args: Parsed command-line arguments
    """
    api_port = args.api_port
    api_url = f"http://localhost:{api_port}"

    processes = []

    try:
        # Start API if requested
        if not args.ui_only:
            print(f"\n🚀 Starting Phase 3 API on port {api_port}...")
            api_env = os.environ.copy()
            api_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "api.phase3_main:app",
                    f"--port={api_port}",
                    "--reload",
                ],
                env=api_env,
            )
            processes.append(("API", api_process))
            print(f"   API process started (PID {api_process.pid})")

            # Wait for API to be ready
            if not _wait_for_api(api_url):
                print("✗ API failed to start within timeout")
                return

            # Trigger demo extraction
            if not args.skip_demo:
                _trigger_extraction_demo(api_url)

        # Start UI if requested
        if not args.api_only:
            print(f"\n🎨 Starting Phase 3 UI on port 8501...")

            ui_env = os.environ.copy()
            ui_env["STREAMLIT_SERVER_HEADLESS"] = "false"
            ui_env["STREAMLIT_LOGGER_LEVEL"] = "warning"

            ui_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    "ui/phase3_extraction.py",
                    f"--server.port=8501",
                ],
                env=ui_env,
            )
            processes.append(("UI", ui_process))
            print(f"   UI process started (PID {ui_process.pid})")

        # Print access information
        print("\n" + "=" * 60)
        print("Phase 3 Services Running:")
        print("=" * 60)

        if not args.ui_only:
            print(f"📡 API:      {api_url}")
            print(f"   Docs:    {api_url}/docs")
            print(f"   ReDoc:   {api_url}/redoc")

        if not args.api_only:
            print(f"🎨 UI:       http://localhost:8501")

        print("\n✓ Press Ctrl+C to stop all services")
        print("=" * 60 + "\n")

        # Wait for interrupt
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down Phase 3 services...")

    finally:
        # Clean up processes
        for service_name, process in processes:
            if process.poll() is None:  # Still running
                print(f"   Stopping {service_name}...")
                process.terminate()

                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        print("✓ All services stopped")


def main() -> None:
    """Parse arguments and start Phase 3."""
    parser = argparse.ArgumentParser(
        description="ScienceKG Phase 3 One-Command Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_phase3.py              # Start API + UI
  python scripts/run_phase3.py --api-only   # API only
  python scripts/run_phase3.py --ui-only    # UI only
  python scripts/run_phase3.py --api-port 9000  # Custom port
        """.strip(),
    )

    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Start only FastAPI server",
    )
    parser.add_argument(
        "--ui-only",
        action="store_true",
        help="Start only Streamlit UI",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="FastAPI port (default: 8000)",
    )
    parser.add_argument(
        "--skip-demo",
        action="store_true",
        help="Skip demo extraction after API ready",
    )

    args = parser.parse_args()

    if args.api_only and args.ui_only:
        print("✗ Cannot use both --api-only and --ui-only")
        sys.exit(1)

    _start(args)


if __name__ == "__main__":
    main()
